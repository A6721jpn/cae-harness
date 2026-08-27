from __future__ import annotations

import copy
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
from febio_cae_harness.reporting import (
    AttemptIdentity,
    EvidenceKind,
    EvidenceProvenance,
    EvidenceReference,
    FreshOutputValidation,
    ReportAuthority,
    ReportAuthorityManager,
    ReportEvidence,
    ResultReport,
    SuccessGateEvaluation,
    assemble_report,
    evaluate_success_gates,
)
from febio_cae_harness.solver import (
    FbsAdapterManager,
    SolverRunResult,
    SolverSupervisor,
)
from febio_cae_harness.solver import (
    headless as headless_module,
)
from febio_cae_harness.solver.runtime import probe_febio
from febio_cae_harness.workspace import AttemptWorkspace, ValidatedCaseWorkspace


class _Adapter:
    def read_fields(self, _path: Path, fields: tuple[str, ...]) -> dict[str, float]:
        return {field: 1.0 for field in fields}


def make_authority(tmp_path: Path) -> tuple[ReportAuthority, SolverRunResult]:
    workspace = ValidatedCaseWorkspace(tmp_path / "tool", tmp_path / "02_CAE")
    case = workspace.create_case("case-a")
    store = EvidenceStore(case, IntentContract())
    store.record_attempt("attempt-a")
    attempt = AttemptWorkspace._from_manager(
        case,
        "attempt-a",
        case.temporary_root / "attempts" / "attempt-a",
    )
    intent = store.issue_intent_snapshot()
    log = "time step 1\ntime = 1.0\nnormal termination\n"
    code = (
        "import os; from pathlib import Path; "
        f"Path(os.environ['FEBIO_CAE_HARNESS_LOG']).write_text({log!r}); "
        "Path(os.environ['FEBIO_CAE_HARNESS_XPLT']).write_bytes(b'synthetic-xplt')"
    )
    input_path = attempt.write_text("model.feb", code)

    class ProbeProcess:
        returncode = 0

        def communicate(self, input: bytes, timeout: float) -> tuple[bytes, bytes]:
            del timeout
            assert input == b"quit\n"
            return b"version 4.12.0\n", b""

    with patch(
        "febio_cae_harness.solver.runtime.subprocess.Popen",
        lambda command, **kwargs: ProbeProcess(),
    ):
        runtime = probe_febio(Path(sys.executable))
    capability = headless_module._issue_launch_capability(
        attempt,
        intent,
        runtime,
        input_path,
        expected_steps=1,
        expected_final_time=1.0,
        timeout_seconds=None,
    )
    root = capability.spec.attempt_root
    fbs = FbsAdapterManager(_Adapter(), "synthetic-runtime", root).issue_authority()
    supervisor = SolverSupervisor(
        capability,
        fbs_adapter=fbs,
        requested_fields=("stress",),
    )
    result = supervisor.run()
    evidence = {kind: root / f"{kind.value}.json" for kind in EvidenceKind}
    for kind, path in evidence.items():
        path.write_text(f"synthetic {kind.value}", encoding="utf-8")
    manager = ReportAuthorityManager(
        supervisor,
        result,
        AttemptIdentity(supervisor._case_id, supervisor._intent_id, supervisor._attempt_id),
    )
    return manager.issue(evidence), result


def live_evaluation(authority: ReportAuthority) -> SuccessGateEvaluation:
    try:
        return evaluate_success_gates(authority)
    except TypeError as error:
        pytest.fail(str(error))


def live_report(authority: ReportAuthority) -> ResultReport:
    try:
        return assemble_report(authority)
    except TypeError as error:
        pytest.fail(str(error))


def test_only_live_report_authority_can_evaluate_and_synthetic_is_unverified(
    tmp_path: Path,
) -> None:
    authority, result = make_authority(tmp_path)

    evaluation = live_evaluation(authority)
    report = live_report(authority)

    assert type(authority) is ReportAuthority
    assert evaluation.checks["report_authority"]
    assert evaluation.checks["owned_process_normal_exit"]
    assert evaluation.checks["fresh_log"]
    assert evaluation.checks["fresh_xplt"]
    assert not evaluation.checks["official_fbs"]
    assert not evaluation.checks["mesh_evidence"]
    assert not evaluation.checks["official_provenance"]
    assert not evaluation.success
    assert not evaluation.verified
    assert report.solver_result is result
    assert report.provenance is not EvidenceProvenance.OFFICIAL
    assert not report.success
    assert not report.verified


@pytest.mark.parametrize(
    "forged",
    [
        True,
        {"official": True, "verified": True, "fresh": True, "satisfies_intent": True},
    ],
)
def test_caller_values_and_legacy_validation_arguments_are_rejected(
    tmp_path: Path, forged: object
) -> None:
    authority, result = make_authority(tmp_path)
    fresh = FreshOutputValidation("attempt-a", result.log_path, result.xplt_path)
    fbs = result.fbs_validation
    reference = EvidenceReference(
        EvidenceKind.MESH,
        "mesh.json",
        "case-a",
        "intent-a",
        "attempt-a",
        verified=True,
        satisfies_intent=True,
        provenance=EvidenceProvenance.OFFICIAL,
    )
    evidence = ReportEvidence(mesh=reference)

    with pytest.raises(TypeError):
        evaluate_success_gates(forged)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        evaluate_success_gates(  # type: ignore[call-arg]
            authority, result, fresh, fbs, evidence, provenance="official"
        )


def test_gate_and_report_objects_cannot_be_publicly_forged_or_rebound(
    tmp_path: Path,
) -> None:
    authority, _ = make_authority(tmp_path / "a")
    other_authority, _ = make_authority(tmp_path / "b")
    evaluation = live_evaluation(authority)
    report = live_report(authority)

    for value in (evaluation, report):
        for operation in (copy.copy, copy.deepcopy):
            with pytest.raises(TypeError):
                operation(value)
        with pytest.raises(TypeError):
            replace(value, provenance=EvidenceProvenance.OFFICIAL)

    with pytest.raises(TypeError):
        evaluate_success_gates(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        assemble_report(other_authority, evaluation)  # type: ignore[call-arg]
