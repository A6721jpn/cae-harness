from __future__ import annotations

import copy
import os
import pickle
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from febio_cae_harness.contracts import IntentContract
from febio_cae_harness.evidence import EvidenceStore
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


def _case(
    tmp_path: Path,
) -> tuple[ReportAuthorityManager, SolverSupervisor, SolverRunResult, dict[EvidenceKind, Path]]:
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
    hardlink = evidence[EvidenceKind.MESH].parent / "hardlink.json"
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
