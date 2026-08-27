from __future__ import annotations

import copy
import os
import pickle
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from febio_cae_harness.reporting import (
    AttemptIdentity,
    EvidenceKind,
    ReportAuthority,
    ReportAuthorityManager,
)
from febio_cae_harness.reporting.authority import _require_issued_authority
from febio_cae_harness.solver import (
    FbsAdapterManager,
    FbsValidation,
    SolverLaunchSpec,
    SolverRunResult,
    SolverSupervisor,
)


class _Adapter:
    def read_fields(self, _path: Path, fields: tuple[str, ...]) -> dict[str, float]:
        return {field: 1.0 for field in fields}


def _case(
    tmp_path: Path,
) -> tuple[ReportAuthorityManager, SolverSupervisor, SolverRunResult, dict[EvidenceKind, Path]]:
    root = tmp_path / "attempt"
    root.mkdir()
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
    return manager, supervisor, result, evidence


def test_issue_binds_all_live_files_and_explicit_provenance(tmp_path: Path) -> None:
    manager, supervisor, result, evidence = _case(tmp_path)
    authority = manager.issue(evidence)

    assert type(authority) is ReportAuthority
    assert authority.manager is manager and authority.supervisor is supervisor
    identity = cast(AttemptIdentity, authority.identity)
    digests = cast(dict[Path, str], authority.digests)
    assert authority.result is result and identity.attempt_id == "attempt-a"
    assert authority.attempt_root == supervisor.spec.attempt_root
    assert authority.evidence == evidence
    assert authority.requested_fields == ("stress",)
    assert authority.runtime_identity == "synthetic-runtime"
    assert authority.provenance == "synthetic-unverified"
    assert len(digests) == 6
    assert all(len(digest) == 64 for digest in digests.values())
    for name in ("success", "succeeded", "fresh", "verified", "official"):
        assert not hasattr(authority, name)


def test_issue_requires_exact_evidence_set_and_live_regular_paths(tmp_path: Path) -> None:
    manager, _, _, evidence = _case(tmp_path)
    with pytest.raises(ValueError):
        manager.issue({EvidenceKind.MESH: evidence[EvidenceKind.MESH]})
    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    invalid = dict(evidence)
    invalid[EvidenceKind.ROI] = outside
    with pytest.raises(ValueError):
        manager.issue(invalid)
    invalid[EvidenceKind.ROI] = evidence[EvidenceKind.MESH]
    with pytest.raises(ValueError):
        manager.issue(invalid)
    hardlink = tmp_path / "attempt" / "hardlink.json"
    os.link(evidence[EvidenceKind.MESH], hardlink)
    invalid[EvidenceKind.ROI] = hardlink
    with pytest.raises(ValueError):
        manager.issue(invalid)


def test_consumer_rejects_other_bindings_and_live_mutations(tmp_path: Path) -> None:
    manager, supervisor, result, evidence = _case(tmp_path)
    authority = manager.issue(evidence)
    with pytest.raises(TypeError):
        _require_issued_authority(authority, object())
    evidence[EvidenceKind.MESH].write_text("changed", encoding="utf-8")
    with pytest.raises(TypeError):
        _require_issued_authority(authority, manager, supervisor, result)
    evidence[EvidenceKind.MESH].write_text("synthetic mesh", encoding="utf-8")
    replacement = tmp_path / "replacement.json"
    replacement.write_text("synthetic mesh", encoding="utf-8")
    os.replace(replacement, evidence[EvidenceKind.MESH])
    with pytest.raises(TypeError):
        _require_issued_authority(authority)


def test_fbs_forgery_and_bad_live_log_are_rejected(tmp_path: Path) -> None:
    manager, supervisor, result, evidence = _case(tmp_path)
    fbs = result.fbs_validation
    assert isinstance(fbs, FbsValidation)
    forged = replace(fbs)
    object.__setattr__(result, "fbs_validation", forged)
    try:
        with pytest.raises(TypeError):
            manager.issue(evidence)
    finally:
        object.__setattr__(result, "fbs_validation", fbs)
    result.log_path.write_text("fatal error", encoding="utf-8")
    with pytest.raises(ValueError):
        manager.issue(evidence)


def test_authorities_reject_forgery_copy_pickle_and_subclassing(tmp_path: Path) -> None:
    manager, _, _, evidence = _case(tmp_path)
    authority = manager.issue(evidence)
    for forged in (object.__new__(ReportAuthority),):
        with pytest.raises(TypeError):
            _require_issued_authority(forged)
    with pytest.raises(TypeError):
        ReportAuthority()
    with pytest.raises(AttributeError):
        object.__setattr__(authority, "success", True)
    for operation in (copy.copy, copy.deepcopy, pickle.dumps):
        with pytest.raises(TypeError):
            operation(authority)
    with pytest.raises(TypeError):

        class AuthorityChild(ReportAuthority):
            pass
