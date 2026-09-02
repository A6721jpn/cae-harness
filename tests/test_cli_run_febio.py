from __future__ import annotations

import hashlib
import importlib
import io
import json
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast

import pytest

from febio_cae_harness import cli as cli_module
from febio_cae_harness.cli_context import CaseContextService, dump_root_capability
from febio_cae_harness.contracts import IntentContract, IntentState
from febio_cae_harness.solver import headless as headless_module
from febio_cae_harness.solver.execution import (
    ExecutionAuthorityError,
    issue_execution_authority,
    reopen_execution_authority,
)
from febio_cae_harness.solver.headless import HeadlessRunDiagnostic, HeadlessRunSession
from febio_cae_harness.solver.log import validate_log
from febio_cae_harness.solver.official_fbs import OfficialFbsRuntime
from febio_cae_harness.solver.runtime import (
    FebioRuntimeDiagnostic,
    RuntimeProbeError,
    probe_febio,
)
from febio_cae_harness.solver.supervisor import SolverSupervisor
from febio_cae_harness.solver.types import SolverClassification, SolverState
from febio_cae_harness.workspace import AttemptWorkspace


def _complete_intent(
    *,
    retry_budget: int | None = None,
    allowed_mesh_changes: object = (),
) -> IntentContract:
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
        allowed_mesh_changes=allowed_mesh_changes,  # type: ignore[arg-type]
        retry_budget=retry_budget,
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
    source_text: str = "<febio_spec version='4.0' />",
) -> tuple[CaseContextService, object, Path]:
    service = _service(root)
    cae_root = root / "02_CAE"
    cae_root.mkdir()
    sources: list[Path] = []
    for name in source_names:
        source = root / name
        source.write_text(source_text, encoding="utf-8")
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


def _issued_official_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModuleType, OfficialFbsRuntime]:
    module = importlib.import_module("febio_cae_harness.solver.official_fbs")
    python_dir = tmp_path / "python313"
    module_dir = tmp_path / "module"
    runtime_dir = tmp_path / "runtime"
    for directory in (python_dir, module_dir, runtime_dir):
        directory.mkdir(parents=True)
    payloads = {
        "python_executable": b"fixture-python-executable",
        "python_dll": b"fixture-python-dll",
        "python_stdlib": b"fixture-python-stdlib",
        "python_path_config": b"fixture-python-path-config",
        "fbs_module": b"fixture-official-fbs-module",
        "zlib": b"fixture-zlib",
    }
    paths = {
        "python_executable": python_dir / "python.exe",
        "python_dll": python_dir / "python313.dll",
        "python_stdlib": python_dir / "python313.zip",
        "python_path_config": python_dir / "python313._pth",
        "fbs_module": module_dir / "fbs.cp313-win_amd64.pyd",
        "zlib": runtime_dir / "zlib1.dll",
    }
    for name, path in paths.items():
        path.write_bytes(payloads[name])
    hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()}
    monkeypatch.setattr(module, "_APPROVED_SHA256", hashes)
    monkeypatch.setattr(
        module,
        "_APPROVED_PYTHON_TREE",
        {path.name: hashes[name] for name, path in paths.items() if name.startswith("python_")},
    )
    monkeypatch.setattr(
        module,
        "_probe_helper",
        lambda held, timeout_seconds: module._ProbeHelperResult(
            {
                "protocol": 1,
                "python": "3.13",
                "module": "fbs",
                "api": ["ReadPlotFile", "vtkExport"],
            },
            "a" * 64,
        ),
    )
    runtime = module.probe_official_fbs_runtime(
        paths["python_executable"],
        paths["fbs_module"],
        paths["zlib"],
    )
    return module, runtime


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


def _retry_arguments(runtime: Path, reservation_id: str, *extra: str) -> list[str]:
    return [
        "retry-febio",
        "--capability-stdin",
        "--case-id",
        "case-a",
        "--reservation-id",
        reservation_id,
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


def test_run_febio_applies_only_current_declared_mesh_patches_in_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = "/febio_spec/Mesh[1]/Elements[1]"
    declaration = {
        "target": target,
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "tet10",
    }
    intent = _complete_intent(
        allowed_mesh_changes={"mesh": {"patches": (declaration,)}},
    )
    service, capability, case_root = _register_case(
        tmp_path,
        intent=intent,
        source_text=("<febio_spec version='4.0'><Mesh><Elements type='tet4'/></Mesh></febio_spec>"),
    )
    runtime = _issued_runtime(tmp_path, monkeypatch)
    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: runtime)
    executed: list[bytes] = []

    def run_synthetic(
        attempt: Any,
        snapshot: Any,
        runtime_diagnostic: FebioRuntimeDiagnostic,
        input_path: Path,
        **kwargs: object,
    ) -> Any:
        del attempt, snapshot, kwargs
        executed.append(input_path.read_bytes())
        log_path = input_path.with_suffix(".log")
        xplt_path = input_path.with_suffix(".xplt")
        log_path.write_bytes(b"synthetic normal termination\n")
        xplt_path.write_bytes(b"synthetic XPLT\n")
        return SimpleNamespace(
            diagnostic=HeadlessRunDiagnostic(
                runtime_identity=runtime_diagnostic,
                state=SolverState.NORMAL_EXIT,
                classification=SolverClassification.FBS_UNVERIFIED,
                return_code=0,
                pid=123,
                log_path=log_path,
                xplt_path=xplt_path,
            ),
            supervisor=None,
            result=None,
        )

    monkeypatch.setattr(cli_module, "run_headless_febio_session", run_synthetic)

    assert cli_module.main(_run_arguments(runtime.path, "--apply-declared-mesh-patches")) == 5

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert payload["error"]["code"] == "FBS_UNAVAILABLE"
    assert len(executed) == 1
    assert b'type="tet10"' in executed[0]
    assert b"type='tet4'" in (case_root / "01_Input" / "model.feb").read_bytes()
    attempts = list((case_root / "90_Temporary" / "attempts").iterdir())
    assert len(attempts) == 1
    assert b'type="tet10"' in (attempts[0] / "model.feb").read_bytes()


def test_run_febio_blocks_nonexact_declared_mesh_patch_before_model_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    declaration = {
        "target": "/febio_spec",
        "mode": "ATTRIBUTE",
        "attribute_name": "version",
        "value": "4.1",
        "material": "must never become an implicit edit",
    }
    intent = _complete_intent(
        allowed_mesh_changes={"mesh": {"patches": (declaration,)}},
    )
    service, capability, case_root = _register_case(tmp_path, intent=intent)
    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)

    assert (
        cli_module.main(
            _run_arguments(
                (tmp_path / "febio.exe").resolve(),
                "--apply-declared-mesh-patches",
            )
        )
        == 4
    )

    payload = json.loads(capsys.readouterr().err)
    assert payload["error"]["code"] == "ASK_AND_BLOCK"
    attempts = list((case_root / "90_Temporary" / "attempts").iterdir())
    assert len(attempts) == 1
    assert not (attempts[0] / "model.feb").exists()


def test_negative_jacobian_repair_proposal_binds_log_and_current_intent(
    tmp_path: Path,
) -> None:
    change = {
        "target": "/febio_spec/Mesh[1]/Elements[1]",
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "tet10",
    }
    repair_evidence = {
        "initial_mesh_valid": True,
        "surrounding_mesh_metrics": {"scaled_jacobian": 0.61},
        "roi_relation_evidence": {"relation": "outside ROI"},
        "contact_relation_evidence": {"relation": "outside contact"},
        "constraint_relation_evidence": {"relation": "outside constraint"},
    }
    intent = _complete_intent(
        retry_budget=1,
        allowed_mesh_changes={
            "mesh": {
                "patches": (change,),
                "negative_jacobian_evidence": repair_evidence,
            }
        },
    )
    service, capability, _ = _register_case(tmp_path, intent=intent)
    opened = service._open_context(capability, "case-a")
    snapshot = opened.store.issue_intent_snapshot()
    log_path = tmp_path / "failed.log"
    log_path.write_text(
        "time step = 2\n"
        "time = 0.25\n"
        "Negative Jacobian determinant = -0.125 at element 17, integration point 3\n",
        encoding="utf-8",
    )
    validation = validate_log(log_path)

    proposal = cli_module._negative_jacobian_repair_proposal(
        snapshot,
        attempt_id="run-failed",
        validation=validation,
    )

    assert proposal is not None
    assert len(proposal.proposal_id) == 64
    assert proposal.changes == {"mesh": {"patches": (change,)}}
    assert snapshot.intent_sha256 in proposal.evidence_ids
    assert hashlib.sha256(log_path.read_bytes()).hexdigest() in proposal.evidence_ids
    assert (
        cli_module._negative_jacobian_repair_proposal(
            snapshot,
            attempt_id="run-failed",
            validation=validation,
        )
        == proposal
    )


def test_run_febio_routes_exact_negative_jacobian_repair_into_retry_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    change = {
        "target": "/febio_spec/Mesh[1]/Elements[1]",
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "tet10",
    }
    intent = _complete_intent(
        retry_budget=1,
        allowed_mesh_changes={
            "mesh": {
                "patches": (change,),
                "negative_jacobian_evidence": {
                    "initial_mesh_valid": True,
                    "surrounding_mesh_metrics": {"scaled_jacobian": 0.61},
                    "roi_relation_evidence": {"relation": "outside ROI"},
                    "contact_relation_evidence": {"relation": "outside contact"},
                    "constraint_relation_evidence": {"relation": "outside constraint"},
                },
            }
        },
    )
    service, capability, _ = _register_case(
        tmp_path,
        intent=intent,
        source_text=("<febio_spec version='4.0'><Mesh><Elements type='tet4'/></Mesh></febio_spec>"),
    )
    runtime = _issued_runtime(tmp_path, monkeypatch)
    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: runtime)

    def run_negative(
        attempt: Any,
        snapshot: Any,
        runtime_diagnostic: FebioRuntimeDiagnostic,
        input_path: Path,
        **kwargs: object,
    ) -> HeadlessRunSession:
        del attempt, snapshot, kwargs
        log_path = input_path.with_suffix(".log")
        xplt_path = input_path.with_suffix(".xplt")
        log_path.write_text(
            "time step = 1\n"
            "time = 1.0\n"
            "Negative Jacobian determinant = -0.125 at element 17, integration point 3\n",
            encoding="utf-8",
        )
        xplt_path.write_bytes(b"synthetic XPLT")
        validation = validate_log(
            log_path,
            expected_steps=1,
            expected_final_time=1.0,
        )
        result = SimpleNamespace(log_validation=validation)
        return HeadlessRunSession(
            supervisor=cast(Any, object()),
            result=cast(Any, result),
            diagnostic=HeadlessRunDiagnostic(
                runtime_identity=runtime_diagnostic,
                state=SolverState.NORMAL_EXIT,
                classification=SolverClassification.NEGATIVE_JACOBIAN,
                return_code=0,
                pid=123,
                log_path=log_path,
                xplt_path=xplt_path,
            ),
        )

    captured_policy: dict[str, object] = {}

    def decide(*args: object, **kwargs: object) -> object:
        del args
        captured_policy.update(kwargs)
        return SimpleNamespace(reservation_required=False)

    monkeypatch.setattr(cli_module, "run_headless_febio_session", run_negative)
    monkeypatch.setattr(cli_module, "decide_retry", decide)

    assert cli_module.main(_run_arguments(runtime.path)) == 4

    response = json.loads(capsys.readouterr().err)
    assert response["error"]["code"] == "SOLVER_FAILED"
    proposal = captured_policy["proposal"]
    assert proposal is not None
    assert len(cast(Any, proposal).proposal_id) == 64
    assert captured_policy["proposal_authority"] is not None


def test_retry_febio_applies_its_exact_reserved_negative_jacobian_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    change = {
        "target": "/febio_spec/Mesh[1]/Elements[1]",
        "mode": "ATTRIBUTE",
        "attribute_name": "type",
        "value": "tet10",
    }
    intent = _complete_intent(
        retry_budget=1,
        allowed_mesh_changes={
            "mesh": {
                "patches": (change,),
                "negative_jacobian_evidence": {
                    "initial_mesh_valid": True,
                    "surrounding_mesh_metrics": {"scaled_jacobian": 0.61},
                    "roi_relation_evidence": {"relation": "outside ROI"},
                    "contact_relation_evidence": {"relation": "outside contact"},
                    "constraint_relation_evidence": {"relation": "outside constraint"},
                },
            }
        },
    )
    service, capability, case_root = _register_case(
        tmp_path,
        intent=intent,
        source_text=("<febio_spec version='4.0'><Mesh><Elements type='tet4'/></Mesh></febio_spec>"),
    )

    class CompletedProbe:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            assert input == b"quit\n"
            assert timeout > 0
            return b"version 4.12.0\n", b""

    with monkeypatch.context() as probe_patch:
        probe_patch.setattr(
            "febio_cae_harness.solver.runtime.subprocess.Popen",
            lambda command, **kwargs: CompletedProbe(),
        )
        runtime = probe_febio(Path(sys.executable))

    launches = 0
    repaired_inputs: list[bytes] = []

    def run_synthetic(
        attempt: AttemptWorkspace,
        snapshot: Any,
        runtime_diagnostic: FebioRuntimeDiagnostic,
        input_path: Path,
        **kwargs: object,
    ) -> Any:
        nonlocal launches
        launches += 1
        if launches == 1:
            script = attempt.write_text(
                "negative.py",
                "import os\n"
                "from pathlib import Path\n"
                "Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text("
                "'time step = 1\\ntime = 1.0\\nNegative Jacobian determinant = -0.125 "
                "at element 17, integration point 3\\n')\n"
                "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'synthetic XPLT')\n",
            )
            session = headless_module.run_headless_febio_session(
                attempt,
                snapshot,
                runtime_diagnostic,
                script,
                expected_steps=1,
                expected_final_time=1.0,
            )
            model_log = input_path.with_suffix(".log")
            model_xplt = input_path.with_suffix(".xplt")
            model_log.write_bytes(session.result.log_path.read_bytes())
            model_xplt.write_bytes(session.result.xplt_path.read_bytes())
            return HeadlessRunSession(
                supervisor=session.supervisor,
                result=session.result,
                diagnostic=HeadlessRunDiagnostic(
                    runtime_identity=runtime_diagnostic,
                    state=session.result.state,
                    classification=session.result.classification,
                    return_code=session.result.return_code,
                    pid=session.result.pid,
                    log_path=model_log,
                    xplt_path=model_xplt,
                ),
            )

        repaired_inputs.append(input_path.read_bytes())
        log_path = input_path.with_suffix(".log")
        xplt_path = input_path.with_suffix(".xplt")
        log_path.write_text(
            "normal termination\ntime step = 1\ntime = 1.0\n",
            encoding="utf-8",
        )
        xplt_path.write_bytes(b"synthetic XPLT")
        return SimpleNamespace(
            diagnostic=HeadlessRunDiagnostic(
                runtime_identity=runtime_diagnostic,
                state=SolverState.NORMAL_EXIT,
                classification=SolverClassification.FBS_UNVERIFIED,
                return_code=0,
                pid=456,
                log_path=log_path,
                xplt_path=xplt_path,
            ),
            supervisor=None,
            result=None,
        )

    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: runtime)
    monkeypatch.setattr(cli_module, "run_headless_febio_session", run_synthetic)

    _set_capability_stdin(monkeypatch, capability)
    assert cli_module.main(_run_arguments(runtime.path)) == 4
    first = json.loads(capsys.readouterr().err)
    reservation_id = first["attempt"]["retry_reservation_id"]
    assert isinstance(reservation_id, str)

    _set_capability_stdin(monkeypatch, capability)
    assert cli_module.main(_retry_arguments(runtime.path, reservation_id)) == 5

    second = json.loads(capsys.readouterr().err)
    assert second["error"]["code"] == "FBS_UNAVAILABLE"
    assert len(repaired_inputs) == 1
    assert b'type="tet10"' in repaired_inputs[0]
    assert b"type='tet4'" in (case_root / "01_Input" / "model.feb").read_bytes()


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


def test_retry_febio_parser_accepts_only_durable_reservation_and_runtime(
    tmp_path: Path,
) -> None:
    parsed = cli_module.build_parser().parse_args(
        _retry_arguments(tmp_path / "febio.exe", "a" * 64)
    )

    assert parsed.command == "retry-febio"
    assert parsed.capability_stdin is True
    assert parsed.case_id == "case-a"
    assert parsed.reservation_id == "a" * 64
    assert not {
        "attempt_id",
        "input",
        "input_name",
        "expected_steps",
        "expected_final_time",
        "requested_field",
        "timeout_seconds",
        "arguments",
    }.intersection(vars(parsed))


@pytest.mark.parametrize("command", ["reconnect-febio", "retry-febio"])
def test_reconnect_and_retry_accept_current_official_fbs_profile_paths(
    tmp_path: Path,
    command: str,
) -> None:
    extra = (
        "--fbs-python",
        str((tmp_path / "python.exe").resolve()),
        "--fbs-module",
        str((tmp_path / "fbs.pyd").resolve()),
        "--fbs-zlib",
        str((tmp_path / "zlib1.dll").resolve()),
    )
    arguments = (
        _reconnect_arguments(tmp_path / "febio.exe", *extra)
        if command == "reconnect-febio"
        else _retry_arguments(tmp_path / "febio.exe", "a" * 64, *extra)
    )

    parsed = cli_module.build_parser().parse_args(arguments)

    assert parsed.fbs_python == (tmp_path / "python.exe").resolve()
    assert parsed.fbs_module == (tmp_path / "fbs.pyd").resolve()
    assert parsed.fbs_zlib == (tmp_path / "zlib1.dll").resolve()


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


def test_reconnect_febio_persists_retry_reservation_before_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, case_root = _register_case(
        tmp_path,
        intent=_complete_intent(retry_budget=1),
    )
    opened = service._open_context(capability, "case-a")
    opened.store.record_attempt("run-existing", {"status": "started"})

    class CompletedProbe:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            assert input == b"quit\n"
            assert timeout > 0
            return b"version 4.12.0\n", b""

    with monkeypatch.context() as probe_patch:
        probe_patch.setattr(
            "febio_cae_harness.solver.runtime.subprocess.Popen",
            lambda command, **kwargs: CompletedProbe(),
        )
        runtime = probe_febio(Path(sys.executable))

    def recover_timeout(
        store: Any,
        snapshot: Any,
        runtime_diagnostic: FebioRuntimeDiagnostic,
        attempt_id: str,
    ) -> Any:
        case = store.case_workspace
        attempt = AttemptWorkspace._from_manager(
            case,
            attempt_id,
            case.temporary_root / "attempts" / attempt_id,
        )
        input_path = attempt.write_text(
            "retry-timeout.feb",
            "import time; time.sleep(30)",
        )
        supervisor = SolverSupervisor(
            headless_module._issue_launch_capability(
                attempt,
                snapshot,
                runtime_diagnostic,
                input_path,
                expected_steps=None,
                expected_final_time=None,
                timeout_seconds=0.1,
            )
        )
        result = supervisor.run()
        assert result.state is SolverState.TIMED_OUT
        assert result.classification is SolverClassification.TIMEOUT
        return SimpleNamespace(
            supervisor=supervisor,
            execution=SimpleNamespace(
                log_path=result.log_path,
                xplt_path=result.xplt_path,
            ),
        )

    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: runtime)
    monkeypatch.setattr(cli_module, "recover_headless_febio", recover_timeout)

    assert cli_module.main(_reconnect_arguments(runtime.path)) == 4

    captured = capsys.readouterr()
    assert captured.out == ""
    response = json.loads(captured.err)
    assert response["error"]["code"] == "SOLVER_FAILED"
    terminal_events = [
        event
        for event in (
            json.loads(line)
            for line in (case_root / "90_Temporary" / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        if event["event_type"] == "run_febio_terminal"
    ]
    assert len(terminal_events) == 1
    terminal = terminal_events[0]["payload"]
    assert {
        key: value for key, value in terminal.items() if key not in {"autonomy", "return_code"}
    } == {
        "attempt_id": "run-existing",
        "classification": "TIMEOUT",
        "official_fbs": False,
        "solver_state": "TIMED_OUT",
        "status": "SOLVER_FAILED",
    }
    assert terminal["return_code"] is None or isinstance(terminal["return_code"], int)
    assert terminal["autonomy"]["decision"] == "RETRY"
    assert terminal["autonomy"]["failure"] == "TIMEOUT"
    assert terminal["autonomy"]["retry_budget"] == 1
    assert terminal["autonomy"]["retry_used"] == 1
    assert len(terminal["autonomy"]["reservation_id"]) == 64


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


def test_reconnect_febio_never_promotes_caller_asserted_official_fbs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, _ = _register_case(tmp_path, intent=_complete_intent())
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
    )
    execution.log_path.write_bytes(b"synthetic normal termination\n")
    execution.xplt_path.write_bytes(b"synthetic XPLT\n")
    result = SimpleNamespace(
        state=SolverState.NORMAL_EXIT,
        classification=SolverClassification.SUCCESS,
        return_code=0,
        pid=123,
        log_path=execution.log_path,
        xplt_path=execution.xplt_path,
        fbs_validation=SimpleNamespace(valid=True, official=True, provenance="official"),
    )
    session = SimpleNamespace(
        supervisor=SimpleNamespace(wait=lambda: result),
        execution=execution,
    )
    issued_authority = object()
    captured: list[tuple[object, object]] = []

    class Manager:
        def __enter__(self) -> Manager:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def issue_authority(self) -> object:
            return issued_authority

    def recover(
        store: object,
        snapshot: object,
        runtime_diagnostic: object,
        attempt_id: str,
        **kwargs: object,
    ) -> object:
        del store, snapshot, runtime_diagnostic
        assert attempt_id == "run-existing"
        captured.append((kwargs["official_fbs_runtime"], kwargs["fbs_adapter"]))
        return session

    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: runtime)
    monkeypatch.setattr(
        cli_module,
        "probe_official_fbs_runtime",
        lambda python, module, zlib: "probe-issued-receipt",
    )
    monkeypatch.setattr(
        cli_module,
        "open_official_fbs_manager",
        lambda receipt, root: Manager(),
    )
    monkeypatch.setattr(cli_module, "recover_headless_febio", recover)
    fbs_args = (
        "--fbs-python",
        str((tmp_path / "python.exe").resolve()),
        "--fbs-module",
        str((tmp_path / "fbs.pyd").resolve()),
        "--fbs-zlib",
        str((tmp_path / "zlib1.dll").resolve()),
    )

    assert cli_module.main(_reconnect_arguments(runtime.path, *fbs_args)) == 4

    captured_output = capsys.readouterr()
    assert captured_output.out == ""
    payload = json.loads(captured_output.err)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "EVIDENCE_INTEGRITY_FAILURE"
    assert captured == [("probe-issued-receipt", issued_authority)]


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
    ) -> Any:
        del snapshot, kwargs
        log_path = input_path.with_suffix(".log")
        xplt_path = input_path.with_suffix(".xplt")
        log_path.write_bytes(b"synthetic normal termination\n")
        xplt_path.write_bytes(b"synthetic XPLT\n")
        return SimpleNamespace(
            diagnostic=HeadlessRunDiagnostic(
                runtime_identity=runtime_diagnostic,
                state=SolverState.NORMAL_EXIT,
                classification=SolverClassification.FBS_UNVERIFIED,
                return_code=0,
                pid=123,
                log_path=log_path,
                xplt_path=xplt_path,
            ),
            supervisor=None,
            result=None,
        )

    monkeypatch.setattr(cli_module, "run_headless_febio_session", run_synthetic)

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


def test_run_febio_never_promotes_diagnostic_boolean_as_official_fbs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, case_root = _register_case(tmp_path, intent=_complete_intent())
    runtime = _issued_runtime(tmp_path, monkeypatch)
    fbs_paths = [
        (tmp_path / "python313" / "python.exe").resolve(),
        (tmp_path / "fbs.cp313-win_amd64.pyd").resolve(),
        (tmp_path / "zlib1.dll").resolve(),
    ]
    issued_authority = object()
    manager_root: Path | None = None
    issued_profiles: list[object] = []
    issue_execution = issue_execution_authority

    class Manager:
        def __enter__(self) -> Manager:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def issue_authority(self) -> object:
            return issued_authority

    def open_manager(receipt: object, attempt_root: Path) -> Manager:
        nonlocal manager_root
        assert receipt == "probe-issued-receipt"
        manager_root = attempt_root
        return Manager()

    def run_official(
        attempt: Any,
        snapshot: Any,
        runtime_diagnostic: FebioRuntimeDiagnostic,
        input_path: Path,
        **kwargs: object,
    ) -> Any:
        del attempt, snapshot
        assert kwargs["fbs_adapter"] is issued_authority
        log_path = input_path.with_suffix(".log")
        xplt_path = input_path.with_suffix(".xplt")
        log_path.write_bytes(b"synthetic normal termination\n")
        xplt_path.write_bytes(b"synthetic XPLT\n")
        return SimpleNamespace(
            diagnostic=HeadlessRunDiagnostic(
                runtime_identity=runtime_diagnostic,
                state=SolverState.NORMAL_EXIT,
                classification=SolverClassification.SUCCESS,
                return_code=0,
                pid=123,
                log_path=log_path,
                xplt_path=xplt_path,
            ),
            supervisor=None,
            result=None,
        )

    def issue_with_profile(*args: object, **kwargs: object) -> object:
        issued_profiles.append(kwargs.pop("official_fbs_runtime"))
        return issue_execution(*args, **kwargs)  # type: ignore[arg-type]

    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: runtime)
    monkeypatch.setattr(
        cli_module,
        "probe_official_fbs_runtime",
        lambda python, module, zlib: "probe-issued-receipt",
        raising=False,
    )
    monkeypatch.setattr(
        cli_module,
        "open_official_fbs_manager",
        open_manager,
        raising=False,
    )
    monkeypatch.setattr(cli_module, "run_headless_febio_session", run_official)
    monkeypatch.setattr(cli_module, "issue_execution_authority", issue_with_profile)

    arguments = _run_arguments(
        runtime.path,
        "--fbs-python",
        str(fbs_paths[0]),
        "--fbs-module",
        str(fbs_paths[1]),
        "--fbs-zlib",
        str(fbs_paths[2]),
    )
    assert cli_module.main(arguments) == 4

    captured_output = capsys.readouterr()
    assert captured_output.out == ""
    payload = json.loads(captured_output.err)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "EVIDENCE_INTEGRITY_FAILURE"
    assert issued_profiles == ["probe-issued-receipt"]
    assert manager_root is not None and manager_root.is_relative_to(case_root)
    events = [
        json.loads(line)
        for line in (case_root / "90_Temporary" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["payload"]["official_fbs"] is False
    assert events[-1]["payload"]["status"] == "EVIDENCE_INTEGRITY_FAILURE"


@pytest.mark.parametrize("physical_success", [False, True], ids=["blocked", "verified"])
def test_exact_official_result_writes_bound_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    physical_success: bool,
) -> None:
    service, capability, case_root = _register_case(tmp_path, intent=_complete_intent())
    opened = service._open_context(capability, "case-a")
    attempt_id = "run-official"
    opened.store.record_attempt(attempt_id, {"status": "started"})
    attempt = AttemptWorkspace._from_manager(
        opened.case,
        attempt_id,
        opened.case.temporary_root / "attempts" / attempt_id,
    )
    log = "time step 1\ntime = 1.0\nnormal termination\n"
    xplt_payload = b"synthetic-xplt"
    code = (
        "import os; from pathlib import Path; "
        f"Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text({log!r}); "
        f"Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes({xplt_payload!r})"
    )
    staged_input = attempt.write_text("solver.py", code)

    class ProbeProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            del timeout
            assert input == b"quit\n"
            return b"version 4.12.0\n", b""

    with monkeypatch.context() as probe_patch:
        probe_patch.setattr(
            "febio_cae_harness.solver.runtime.subprocess.Popen",
            lambda command, **kwargs: ProbeProcess(),
        )
        runtime = probe_febio(Path(sys.executable))

    official_module, official_runtime = _issued_official_runtime(
        tmp_path / "official-runtime",
        monkeypatch,
    )
    model_manifest = {
        "available_fields": [{"index": 0, "name": "stress"}],
        "element_count": 1,
        "node_count": 4,
        "requested_fields": {
            "stress": {
                "association": "CELL_DATA",
                "component_index": 0,
                "component_name": "stress",
                "components": 9,
                "field_index": 0,
                "tensor_type": "DATA_TENSOR2",
                "vtk_name": "stress",
            }
        },
        "state_count": 1,
        "state_times": [0.0],
    }
    monkeypatch.setattr(
        official_module,
        "_invoke_helper",
        lambda checked, path, fields, root: {
            "protocol": 1,
            "available_fields": ["stress"],
            "model_manifest": model_manifest,
            "model_sha256": official_module._model_manifest_sha256(model_manifest),
            "values": {
                "stress": {
                    "components": 9,
                    "count": 9,
                    "entity_count": 1,
                    "field_index": 0,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "state_count": 1,
                }
            },
            "non_finite_fields": [],
            "xplt_sha256": hashlib.sha256(xplt_payload).hexdigest(),
        },
    )
    snapshot = opened.store.issue_intent_snapshot()
    execution = issue_execution_authority(
        attempt,
        snapshot,
        runtime,
        staged_input,
        requested_fields=("stress",),
        expected_steps=1,
        expected_final_time=1.0,
        timeout_seconds=None,
        official_fbs_runtime=official_runtime,
    )
    if physical_success:
        physical_module = importlib.import_module("febio_cae_harness.reporting.physical")
        synthetic_evaluation = physical_module.PhysicalEvidenceEvaluation(
            {kind: True for kind in ("mesh", "jacobian", "roi", "evaluation")},
            {
                kind: {"synthetic_plumbing": True}
                for kind in ("mesh", "jacobian", "roi", "evaluation")
            },
            {kind: () for kind in ("mesh", "jacobian", "roi", "evaluation")},
        )
        monkeypatch.setattr(
            physical_module,
            "_evaluate_physical_payload",
            lambda **kwargs: synthetic_evaluation,
        )

    with official_module.open_official_fbs_manager(
        official_runtime,
        attempt.root,
    ) as fbs_manager:
        session = headless_module.run_headless_febio_session(
            attempt,
            snapshot,
            runtime,
            staged_input,
            requested_fields=("stress",),
            expected_steps=1,
            expected_final_time=1.0,
            fbs_adapter=fbs_manager.issue_authority(),
        )
        solver = {
            "classification": session.result.classification.value,
            "return_code": session.result.return_code,
            "state": session.result.state.value,
        }
        assert cli_module._complete_official_report(
            command="run-febio",
            store=opened.store,
            attempt=attempt,
            snapshot=snapshot,
            execution=execution,
            supervisor=session.supervisor,
            result=session.result,
            staged_input=staged_input,
            attempt_payload={
                "attempt_id": attempt_id,
                "official_fbs": False,
                "status": "SUCCESS",
            },
            solver=solver,
        ) == (0 if physical_success else 6)

    captured = capsys.readouterr()
    assert (captured.err == "") is physical_success
    assert (captured.out == "") is not physical_success
    response = json.loads(captured.out if physical_success else captured.err)
    assert response["ok"] is physical_success
    if not physical_success:
        assert response["error"]["code"] == "REPORT_BLOCKED"
    assert response["attempt"] == {
        "attempt_id": attempt_id,
        "official_fbs": True,
        "status": "SUCCESS" if physical_success else "REPORT_BLOCKED",
    }
    assert response["report"]["success"] is physical_success
    assert response["report"]["verified"] is physical_success
    assert response["report"]["provenance"] == "official"
    if physical_success:
        assert response["report"]["failed_checks"] == []
    else:
        assert set(response["report"]["failed_checks"]) >= {
            "mesh_evidence",
            "jacobian_evidence",
            "roi_evidence",
            "evaluation_evidence",
        }
    assert not _contains_path_key(response)
    assert str(tmp_path) not in (captured.out + captured.err)

    attempt_root = case_root / "90_Temporary" / "attempts" / attempt_id
    report = json.loads((attempt_root / "report.json").read_text(encoding="utf-8"))
    assert report["success"] is physical_success
    assert report["verified"] is physical_success
    assert report["provenance"] == "official"
    for kind in ("mesh", "jacobian", "roi", "evaluation"):
        evidence = json.loads(
            (attempt_root / "report-evidence" / f"{kind}.json").read_text(encoding="utf-8")
        )
        assert evidence["kind"] == kind
        assert evidence["verified"] is physical_success
        assert evidence["satisfies_intent"] is physical_success
    events = [
        json.loads(line)
        for line in (case_root / "90_Temporary" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["event_type"] == "run_febio_terminal"
    assert events[-1]["payload"]["official_fbs"] is True
    assert events[-1]["payload"]["status"] == ("SUCCESS" if physical_success else "REPORT_BLOCKED")


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
    ) -> Any:
        del attempt, snapshot, kwargs
        return SimpleNamespace(
            diagnostic=HeadlessRunDiagnostic(
                runtime_identity=runtime_diagnostic,
                state=SolverState.FAILED,
                classification=SolverClassification.FATAL,
                return_code=1,
                pid=123,
                log_path=input_path.with_suffix(".log"),
                xplt_path=input_path.with_suffix(".xplt"),
            ),
            supervisor=None,
            result=None,
        )

    monkeypatch.setattr(cli_module, "run_headless_febio_session", fail_synthetic)

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


def test_run_febio_persists_retry_reservation_before_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, case_root = _register_case(
        tmp_path,
        intent=_complete_intent(retry_budget=1),
    )

    class CompletedProbe:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            assert input == b"quit\n"
            assert timeout > 0
            return b"version 4.12.0\n", b""

    with monkeypatch.context() as probe_patch:
        probe_patch.setattr(
            "febio_cae_harness.solver.runtime.subprocess.Popen",
            lambda command, **kwargs: CompletedProbe(),
        )
        runtime = probe_febio(Path(sys.executable))

    def run_timeout(
        attempt: AttemptWorkspace,
        snapshot: Any,
        runtime_diagnostic: FebioRuntimeDiagnostic,
        input_path: Path,
        **kwargs: object,
    ) -> Any:
        del input_path, kwargs
        timeout_input = attempt.write_text(
            "retry-timeout.py",
            "import time; time.sleep(30)",
        )
        return headless_module.run_headless_febio_session(
            attempt,
            snapshot,
            runtime_diagnostic,
            timeout_input,
            timeout_seconds=0.1,
        )

    _set_capability_stdin(monkeypatch, capability)
    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: runtime)
    monkeypatch.setattr(cli_module, "run_headless_febio_session", run_timeout)

    assert cli_module.main(_run_arguments(runtime.path)) == 4

    captured = capsys.readouterr()
    assert captured.out == ""
    response = json.loads(captured.err)
    assert response["error"]["code"] == "SOLVER_FAILED"
    attempts = list((case_root / "90_Temporary" / "attempts").iterdir())
    assert len(attempts) == 1
    attempt_id = attempts[0].name
    terminal_events = [
        event
        for event in (
            json.loads(line)
            for line in (case_root / "90_Temporary" / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        if event["event_type"] == "run_febio_terminal"
    ]
    assert len(terminal_events) == 1
    terminal = terminal_events[0]["payload"]
    assert {
        key: value for key, value in terminal.items() if key not in {"autonomy", "return_code"}
    } == {
        "attempt_id": attempt_id,
        "classification": "TIMEOUT",
        "official_fbs": False,
        "solver_state": "TIMED_OUT",
        "status": "SOLVER_FAILED",
    }
    assert terminal["return_code"] is None or isinstance(terminal["return_code"], int)
    assert terminal["autonomy"]["decision"] == "RETRY"
    assert terminal["autonomy"]["failure"] == "TIMEOUT"
    assert terminal["autonomy"]["retry_budget"] == 1
    assert terminal["autonomy"]["retry_used"] == 1
    assert len(terminal["autonomy"]["reservation_id"]) == 64


def test_retry_febio_claims_reservation_and_reuses_parent_execution_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service, capability, case_root = _register_case(
        tmp_path,
        intent=_complete_intent(retry_budget=1),
    )

    class CompletedProbe:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            assert input == b"quit\n"
            assert timeout > 0
            return b"version 4.12.0\n", b""

    with monkeypatch.context() as probe_patch:
        probe_patch.setattr(
            "febio_cae_harness.solver.runtime.subprocess.Popen",
            lambda command, **kwargs: CompletedProbe(),
        )
        runtime = probe_febio(Path(sys.executable))

    launches: list[tuple[str, str, dict[str, object]]] = []

    def run_timeout(
        attempt: AttemptWorkspace,
        snapshot: Any,
        runtime_diagnostic: FebioRuntimeDiagnostic,
        input_path: Path,
        **kwargs: object,
    ) -> Any:
        launches.append((attempt.attempt_id, input_path.name, dict(kwargs)))
        timeout_input = attempt.write_text(
            f"retry-timeout-{len(launches)}.py",
            "import time; time.sleep(30)",
        )
        return headless_module.run_headless_febio_session(
            attempt,
            snapshot,
            runtime_diagnostic,
            timeout_input,
            timeout_seconds=0.1,
        )

    monkeypatch.setattr(cli_module, "_case_service", lambda: service)
    monkeypatch.setattr(cli_module, "probe_febio", lambda path: runtime)
    monkeypatch.setattr(cli_module, "run_headless_febio_session", run_timeout)

    _set_capability_stdin(monkeypatch, capability)
    assert cli_module.main(_run_arguments(runtime.path)) == 4
    first_response = json.loads(capsys.readouterr().err)
    reservation_id = first_response["attempt"]["retry_reservation_id"]
    assert isinstance(reservation_id, str)
    assert len(reservation_id) == 64

    original_reopen_execution_authority = reopen_execution_authority

    def reject_parent_execution(*args: object, **kwargs: object) -> Any:
        del args, kwargs
        raise ExecutionAuthorityError("synthetic parent reauthentication failure")

    monkeypatch.setattr(
        cli_module,
        "reopen_execution_authority",
        reject_parent_execution,
    )
    _set_capability_stdin(monkeypatch, capability)
    assert cli_module.main(_retry_arguments(runtime.path, reservation_id)) == 4
    setup_failure = json.loads(capsys.readouterr().err)
    assert setup_failure["command"] == "retry-febio"
    assert setup_failure["error"]["code"] == "EXECUTION_FAILED"
    assert len(launches) == 1
    setup_terminal_events = [
        json.loads(line)
        for line in (case_root / "90_Temporary" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line)["event_type"] == "run_febio_terminal"
    ]
    assert len(setup_terminal_events) == 1
    assert "autonomy" in setup_terminal_events[0]["payload"]

    monkeypatch.setattr(
        cli_module,
        "reopen_execution_authority",
        original_reopen_execution_authority,
    )
    _set_capability_stdin(monkeypatch, capability)
    assert cli_module.main(_retry_arguments(runtime.path, reservation_id)) == 4
    retry_response = json.loads(capsys.readouterr().err)
    retry_attempt_id = f"retry-{reservation_id}"
    assert retry_response["command"] == "retry-febio"
    assert retry_response["error"]["code"] == "SOLVER_FAILED"
    assert retry_response["attempt"]["attempt_id"] == retry_attempt_id
    assert retry_response["attempt"]["official_fbs"] is False

    assert len(launches) == 2
    assert launches[1] == (
        retry_attempt_id,
        "model.feb",
        {
            "expected_final_time": 1.0,
            "expected_steps": 1,
            "requested_fields": ("displacement",),
            "timeout_seconds": None,
        },
    )
    attempts = sorted((case_root / "90_Temporary" / "attempts").iterdir())
    assert {attempt.name for attempt in attempts} == {
        launches[0][0],
        retry_attempt_id,
    }
    retry_attempt = case_root / "90_Temporary" / "attempts" / retry_attempt_id
    retry_record = json.loads((retry_attempt / "ATTEMPT.json").read_text(encoding="utf-8"))
    assert retry_record["payload"]["status"] == "retry_claimed"
    assert retry_record["payload"]["retry_claim"]["reservation_id"] == reservation_id
    assert retry_record["payload"]["retry_claim"]["parent_attempt_id"] == launches[0][0]
    assert (retry_attempt / "model.feb").read_text(encoding="utf-8") == (
        case_root / "01_Input" / "model.feb"
    ).read_text(encoding="utf-8")
    setup_events = [
        json.loads(line)
        for line in (case_root / "90_Temporary" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line)["event_type"] == "retry_setup_started"
    ]
    assert len(setup_events) == 1
    assert setup_events[0]["payload"] == {
        "attempt_id": retry_attempt_id,
        "intent_sha256": retry_record["payload"]["retry_claim"]["intent_sha256"],
        "reservation_id": reservation_id,
    }

    terminal_events = [
        event
        for event in (
            json.loads(line)
            for line in (case_root / "90_Temporary" / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        if event["event_type"] == "run_febio_terminal"
    ]
    assert len(terminal_events) == 2
    assert "autonomy" in terminal_events[0]["payload"]
    assert terminal_events[1]["payload"]["attempt_id"] == retry_attempt_id
    assert terminal_events[1]["payload"]["status"] == "SOLVER_FAILED"
    assert "autonomy" not in terminal_events[1]["payload"]

    _set_capability_stdin(monkeypatch, capability)
    assert cli_module.main(_retry_arguments(runtime.path, reservation_id)) == 4
    replay_response = json.loads(capsys.readouterr().err)
    assert replay_response["command"] == "retry-febio"
    assert replay_response["error"]["code"] == "RETRY_ALREADY_STARTED"
    assert replay_response["attempt"]["attempt_id"] == retry_attempt_id
    assert len(launches) == 2
    replay_terminal_events = [
        json.loads(line)
        for line in (case_root / "90_Temporary" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line)["event_type"] == "run_febio_terminal"
    ]
    assert replay_terminal_events == terminal_events

    (retry_attempt / "execution.json").unlink()
    _set_capability_stdin(monkeypatch, capability)
    assert cli_module.main(_retry_arguments(runtime.path, reservation_id)) == 4
    missing_execution_response = json.loads(capsys.readouterr().err)
    assert missing_execution_response["command"] == "retry-febio"
    assert missing_execution_response["error"]["code"] == "RETRY_ALREADY_STARTED"
    assert len(launches) == 2
    after_mutation_terminals = [
        json.loads(line)
        for line in (case_root / "90_Temporary" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line)["event_type"] == "run_febio_terminal"
    ]
    assert after_mutation_terminals == terminal_events
