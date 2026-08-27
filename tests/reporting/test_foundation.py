from __future__ import annotations

import copy
import sys
from dataclasses import replace
from pathlib import Path

import pytest

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
    SolverLaunchSpec,
    SolverRunResult,
    SolverSupervisor,
)


class _Adapter:
    def read_fields(self, _path: Path, fields: tuple[str, ...]) -> dict[str, float]:
        return {field: 1.0 for field in fields}


def make_authority(tmp_path: Path) -> tuple[ReportAuthority, SolverRunResult]:
    root = tmp_path / "attempt"
    root.mkdir(parents=True)
    input_path = root / "model.feb"
    log_path = root / "result.log"
    xplt_path = root / "result.xplt"
    input_path.write_text("synthetic", encoding="utf-8")
    log = "time step 1\ntime = 1.0\nnormal termination\n"
    code = (
        "from pathlib import Path; "
        f"Path({str(log_path)!r}).write_text({log!r}); "
        f"Path({str(xplt_path)!r}).write_bytes(b'synthetic-xplt')"
    )
    spec = SolverLaunchSpec(
        executable=Path(sys.executable),
        input_path=input_path,
        attempt_root=root,
        log_path=log_path,
        xplt_path=xplt_path,
        arguments=("-c", code),
        expected_steps=1,
        expected_final_time=1.0,
        requested_fields=("stress",),
    )
    fbs = FbsAdapterManager(_Adapter(), "synthetic-runtime", root).issue_authority()
    supervisor = SolverSupervisor(
        spec,
        case_id="case-a",
        intent_id="intent-a",
        attempt_id="attempt-a",
        fbs_adapter=fbs,
        requested_fields=("stress",),
    )
    result = supervisor.run()
    evidence = {kind: root / f"{kind.value}.json" for kind in EvidenceKind}
    for kind, path in evidence.items():
        path.write_text(f"synthetic {kind.value}", encoding="utf-8")
    manager = ReportAuthorityManager(
        supervisor, result, AttemptIdentity("case-a", "intent-a", "attempt-a")
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
