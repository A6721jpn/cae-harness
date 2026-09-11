"""Small synthetic printed observations, with no CaseSpec/revision or STEP fixture."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from typing import Any

import pytest

from febio_cae.adapters.febio.reported_norms import (
    ReportedNormInputPolicy,
    ReportedNormInvocation,
    assess_reported_norm_observations,
)
from febio_cae.domain import (
    CompatibilityProfile,
    FileEntry,
    NumericalProfileRef,
    ProcessIdentity,
    Quantity,
    ResolvedFileContent,
    SolverControl,
    SolverPolicy,
    TimeIncrementPolicy,
    ToolIdentity,
)
from febio_cae.domain.canonical import canonical_bytes


def _resolved(path: str, role: str, content: bytes) -> ResolvedFileContent:
    return ResolvedFileContent(
        FileEntry(path, hashlib.sha256(content).hexdigest(), len(content), role), content
    )


def _context(
    *, full_newton: bool = False
) -> tuple[
    ResolvedFileContent, ReportedNormInputPolicy, CompatibilityProfile, ReportedNormInvocation
]:
    max_ups = 0 if full_newton else 10
    controls: tuple[SolverControl, ...] = (
        SolverControl("dtol", Quantity(0.001, "1")),
        SolverControl("etol", Quantity(0.01, "1")),
        SolverControl("rtol", Quantity(0.001, "1")),
        SolverControl("min_residual", Quantity(0, "1")),
        SolverControl("max_refs", 50),
        SolverControl("reform_augment", True),
        SolverControl("max_ups", max_ups),
        SolverControl("minaug", 0),
        SolverControl("maxaug", 10),
        SolverControl("laugon", 1),
        SolverControl("tolerance", Quantity(0.01, "1")),
        SolverControl("gaptol", Quantity(1e-8, "m")),
        SolverControl("two_pass", False),
    )
    if full_newton:
        controls += (SolverControl("symmetric_stiffness", 0),)
    # This immutable toy record declares the numerical controls; it is not a
    # production registry record or a real FEBio execution receipt.
    record = canonical_bytes({"controls": [c.to_dict() for c in controls]})
    solver_policy = SolverPolicy(
        NumericalProfileRef("local_synthetic_solver", "solver", hashlib.sha256(record).hexdigest()),
        controls,
        TimeIncrementPolicy(
            Quantity(0.1, "s"), Quantity(0.1, "s"), Quantity(0.1, "s"), False, 10, 0, ()
        ),
        (),
    )
    policy = ReportedNormInputPolicy(solver_policy, Quantity(0, "s"), Quantity(1, "s"))
    tool = ToolIdentity("synthetic_solver", "4.12.0", hashlib.sha256(b"toy solver").hexdigest())
    profile = CompatibilityProfile("local_synthetic_compatibility", tool, tool, (), (), ())
    argv = (
        "synthetic-febio",
        "-i",
        "input/case.feb",
        "-o",
        "output/solver.log",
        "-p",
        "output/results.xplt",
        "-noappend",
        "-noconfig",
    )
    invocation = ReportedNormInvocation(
        tool,
        argv,
        ProcessIdentity(
            argv[0], tool.executable_digest, argv, "local-synthetic-run", 1, "toy-only"
        ),
    )
    values = {c.name: c.value for c in controls}

    def xml(names: tuple[str, ...]) -> str:
        rows = []
        for name in names:
            value = values[name]
            token = (
                format(value.to_si().value, ".17g")
                if isinstance(value, Quantity)
                else str(int(value))
            )
            rows.append(f"<{name}>{token}</{name}>")
        return "".join(rows)

    method = "Broyden" if full_newton else "BFGS"
    symmetry = "<symmetric_stiffness>0</symmetric_stiffness>" if full_newton else ""
    content = (
        '<febio_spec version="4.0"><Module type="solid"/><Control><analysis type="static"/>'
        '<time_steps>10</time_steps><step_size>0.1</step_size><solver type="solid">'
        + xml(("dtol", "etol", "rtol", "min_residual", "max_refs", "reform_augment"))
        + symmetry
        + f'<qn_method type="{method}"><max_ups>{max_ups}</max_ups></qn_method></solver></Control>'
        + '<Contact><contact type="sliding-elastic">'
        + xml(("laugon", "tolerance", "gaptol", "minaug", "maxaug", "two_pass"))
        + "</contact></Contact></febio_spec>"
    ).encode("ascii")
    return _resolved("input/case.feb", "input", content), policy, profile, invocation


def _log(prefix: str = "", *, full_newton: bool = False) -> ResolvedFileContent:
    echoes = {
        "Module type": "solid",
        "analysis": "STATIC (0)",
        "time_steps": "10",
        "step_size": "0.1",
        "Auto time stepper activated": "no",
        "dtol": "0.001",
        "etol": "0.01",
        "rtol": "0.001",
        "min_residual": "0",
        "max_refs": "50",
        "reform_augment": "yes (1)",
        "laugon": "AUGLAG (1)",
        "tolerance": "0.01",
        "gaptol": "1e-08",
        "minaug": "0",
        "maxaug": "10",
        "two_pass": "no (0)",
    }
    lines = [
        "version 4.12.0",
        *(f"{key} .... : {value}" for key, value in echoes.items()),
        *(["symmetric_stiffness .... : non-symmetric (0)"] if full_newton else []),
        "contact interface 1 - Type: sliding-elastic",
        *(["symmetric_stiffness .... : yes (1)"] if full_newton else []),
    ]
    for step in range(1, 11):
        time = str(step / 10) if step < 10 else "1"
        nonlinear = [
            "1",
            f"Nonlinear solution status: time= {time}",
            "stiffness updates = 1",
            "right hand side evaluations = 3",
            "stiffness matrix reformations = 1",
            "step from line search = 1.000000",
            "convergence norms : INITIAL CURRENT REQUIRED",
            *(
                f"{name} 1.000000e+00 1.000000e-04 1.000000e-03"
                for name in ("residual", "energy", "displacement")
            ),
        ]
        gap = "2.000000e-08" if prefix and step == 9 else "1.000000e-09"
        augmentation = [
            "..... augmentation # 1",
            "sliding interface # 1",
            "CURRENT REQUIRED",
            "D multiplier : 1.000000e-03 1.000000e-02",
            f"maximum gap : {gap} 1.000000e-08",
        ]
        summary = ["convergence summary", "number of iterations : 1", "number of reformations : 0"]
        lines.append(f"===== beginning time step {step} : {time} =====")
        if step == 10 and prefix:
            if prefix == "nonlinear":
                lines += nonlinear[:8]
            elif prefix == "augmentation":
                lines += nonlinear + augmentation[:4]
            elif prefix == "summary":
                lines += nonlinear + augmentation + summary[:2]
            else:
                lines += nonlinear + augmentation[:1] + ["sliding interface # 2"]
            break
        lines += nonlinear + augmentation + summary + [f"------- converged at time : {time}"]
    return _resolved(
        "output/solver.log", "solver_log", ("\r\n".join(lines) + "\r\n").encode("ascii")
    )


def _rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for block in report["blocks"] for row in block.get("rows", [])]


def test_manual_observations_complete_without_case_revision() -> None:
    source, policy, profile, invocation = _context()
    log = _log()
    report = assess_reported_norm_observations(
        source, log, policy=policy, profile=profile, invocation=invocation
    ).to_dict()
    assert report["admission"] == "SUPPORTED" and report["final_status"] == "PASS"
    assert report["native_qualification"] == "UNVERIFIED" and not report["reasons"]
    assert (
        report["expected_steps"]
        == [step["step"] for step in report["accepted_steps"]]
        == list(range(1, 11))
    )
    assert report["bindings"] == {
        "input": source.entry.to_dict(),
        "log": log.entry.to_dict(),
        "solver_policy_digest": hashlib.sha256(policy.solver_policy.to_bytes()).hexdigest(),
        "solver_profile": policy.solver_policy.profile.to_dict(),
        "motion_start": {"value": policy.motion_start.value, "unit": policy.motion_start.unit},
        "motion_end": {"value": policy.motion_end.value, "unit": policy.motion_end.unit},
        "profile_id": profile.profile_id,
        "profile_digest": hashlib.sha256(profile.to_bytes()).hexdigest(),
        "solver": profile.solver.to_dict(),
        "invocation": {
            "tool": invocation.tool.to_dict(),
            "argv": list(invocation.argv),
            "process": invocation.process.to_dict() if invocation.process else None,
        },
    }
    for row in _rows(report):
        assert row["status"] == "PASS"
        assert log.content[slice(*row["current_span"])] == row["current"].encode("ascii")
    digest = report.pop("report_digest")
    assert digest == hashlib.sha256(canonical_bytes(report)).hexdigest()


def test_full_newton_observations_require_exact_pair_and_solver_symmetry_echo() -> None:
    source, policy, profile, invocation = _context(full_newton=True)
    report = assess_reported_norm_observations(
        source,
        _log(full_newton=True),
        policy=policy,
        profile=profile,
        invocation=invocation,
    ).to_dict()

    assert report["admission"] == "SUPPORTED" and report["final_status"] == "PASS"
    assert report["native_qualification"] == "UNVERIFIED" and not report["reasons"]
    assert _rows(report) and all(row["status"] == "PASS" for row in _rows(report))


@pytest.mark.parametrize(
    "contradiction", ["solver-echo-wrong", "solver-echo-missing", "input-method"]
)
def test_full_newton_observations_reject_wrong_or_missing_solver_pair_evidence(
    contradiction: str,
) -> None:
    source, policy, profile, invocation = _context(full_newton=True)
    log = _log(full_newton=True)
    if contradiction == "solver-echo-wrong":
        log = _resolved(
            "output/solver.log",
            "solver_log",
            log.content.replace(
                b"symmetric_stiffness .... : non-symmetric (0)",
                b"symmetric_stiffness .... : yes (1)",
            ),
        )
    elif contradiction == "solver-echo-missing":
        log = _resolved(
            "output/solver.log",
            "solver_log",
            log.content.replace(b"symmetric_stiffness .... : non-symmetric (0)\r\n", b""),
        )
    else:
        source = _resolved(
            "input/case.feb",
            "input",
            source.content.replace(b'type="Broyden"', b'type="BFGS"'),
        )
    report = assess_reported_norm_observations(
        source, log, policy=policy, profile=profile, invocation=invocation
    ).to_dict()

    assert report["admission"] == report["final_status"] == "UNVERIFIED"
    assert report["reasons"] and _rows(report)
    assert all(row["status"] == "UNVERIFIED" for row in _rows(report))


def test_native_contact_heading_is_admitted_without_hiding_final_failure() -> None:
    source, policy, profile, invocation = _context()
    heading = b"CONTACT INTERFACE DATA\r\n"
    declaration = b"contact interface 1 - Type: sliding-elastic\r\n"

    valid_log = _resolved(
        "output/solver.log",
        "solver_log",
        _log().content.replace(declaration, heading + declaration, 1),
    )
    valid = assess_reported_norm_observations(
        source, valid_log, policy=policy, profile=profile, invocation=invocation
    ).to_dict()
    assert valid["admission"] == "SUPPORTED" and valid["final_status"] == "PASS"
    assert _rows(valid) and all(row["status"] == "PASS" for row in _rows(valid))

    failing_log = _resolved(
        "output/solver.log",
        "solver_log",
        _log("nonlinear").content.replace(declaration, heading + declaration, 1),
    )
    failing = assess_reported_norm_observations(
        source, failing_log, policy=policy, profile=profile, invocation=invocation
    ).to_dict()
    assert failing["admission"] == "SUPPORTED" and failing["final_status"] == "FAIL"
    assert any(row["status"] == "FAIL" for row in _rows(failing))


@pytest.mark.parametrize(
    "boundary", ["duplicate", "after-declaration", "inside-increment", "after-increment", "unknown"]
)
def test_native_contact_heading_is_unverified_outside_preamble(boundary: str) -> None:
    source, policy, profile, invocation = _context()
    content = _log().content
    heading = b"CONTACT INTERFACE DATA\r\n"
    declaration = b"contact interface 1 - Type: sliding-elastic\r\n"
    if boundary == "duplicate":
        content = content.replace(declaration, heading + heading + declaration, 1)
    elif boundary == "after-declaration":
        content = content.replace(declaration, declaration + heading, 1)
    elif boundary == "inside-increment":
        marker = b"===== beginning time step 1 : 0.1 =====\r\n"
        content = content.replace(marker, marker + heading, 1)
    elif boundary == "after-increment":
        marker = b"------- converged at time : 0.1\r\n"
        content = content.replace(marker, marker + heading, 1)
    else:
        content = content.replace(
            declaration, b"CONTACT INTERFACE DATA EXTRA\r\n" + declaration, 1
        )
    log = _resolved("output/solver.log", "solver_log", content)

    report = assess_reported_norm_observations(
        source, log, policy=policy, profile=profile, invocation=invocation
    ).to_dict()
    assert report["admission"] == "SUPPORTED" and report["final_status"] == "UNVERIFIED"
    assert report["reasons"] and _rows(report)
    assert all(row["status"] == "UNVERIFIED" for row in _rows(report))


@pytest.mark.parametrize("contradiction", ["policy", "echo", "invocation"])
def test_manual_observations_admission_contradiction(contradiction: str) -> None:
    source, policy, profile, invocation = _context()
    log = _log()
    if contradiction == "policy":
        changed = tuple(
            replace(c, value=Quantity(0.002, "1")) if c.name == "dtol" else c
            for c in policy.solver_policy.controls
        )
        policy = replace(policy, solver_policy=replace(policy.solver_policy, controls=changed))
    elif contradiction == "echo":
        log = _resolved(
            "output/solver.log",
            "solver_log",
            log.content.replace(b"dtol .... : 0.001", b"dtol .... : 0.002"),
        )
    else:
        assert invocation.process is not None
        invocation = replace(invocation, process=replace(invocation.process, argv=("other",)))
    report = assess_reported_norm_observations(
        source, log, policy=policy, profile=profile, invocation=invocation
    ).to_dict()
    assert report["admission"] == report["final_status"] == "UNVERIFIED"
    assert report["reasons"] and _rows(report)
    assert all(row["status"] == "UNVERIFIED" for row in _rows(report))


@pytest.mark.parametrize("prefix", ["nonlinear", "augmentation", "summary", "malformed"])
def test_manual_observations_prior_fail_and_incomplete_prefix(prefix: str) -> None:
    source, policy, profile, invocation = _context()
    report = assess_reported_norm_observations(
        source, _log(prefix), policy=policy, profile=profile, invocation=invocation
    ).to_dict()
    assert report["admission"] == "SUPPORTED" and report["reasons"]
    assert [step["step"] for step in report["accepted_steps"]] == list(range(1, 10))
    if prefix == "malformed":
        assert report["final_status"] == "UNVERIFIED"
        assert all(row["status"] == "UNVERIFIED" for row in _rows(report))
    else:
        assert report["final_status"] == "FAIL"
        assert any(row["status"] == "FAIL" for row in _rows(report))
        incomplete = next(
            block for block in report["blocks"] if block["kind"] == f"incomplete_{prefix}"
        )
        assert all(row["status"] == "UNVERIFIED" for row in incomplete.get("rows", []))
