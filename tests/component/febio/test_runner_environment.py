"""Real child-process worker environment; synthetic Python, not native FEBio."""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from febio_cae.adapters.febio.runner import RunnerAdapter
from febio_cae.domain import PortError, PortErrorCategory, RunState

from .runner_fixture import _compiled, _owner, _Ownership
from .test_runner_authority import _finish
from .test_runner_job import _cleanup


def test_issued_child_receives_worker_limits_without_mutating_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision, _, _, bundle, store = _compiled(tmp_path)
    expected = {
        "OMP_NUM_THREADS": "1",
        "OMP_THREAD_LIMIT": "1",
        "MKL_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_DYNAMIC": "FALSE",
        "MKL_DYNAMIC": "FALSE",
    }
    for name in expected:
        monkeypatch.setenv(name, "8" if name.endswith("THREADS") else "TRUE")
    monkeypatch.setenv("CAE_TEST_ENV_SENTINEL", "parent-value")
    inherited = {name: os.environ[name] for name in expected}
    keys = (*expected, "CAE_TEST_ENV_SENTINEL")
    code = f"import os,json;print(json.dumps({{k:os.environ.get(k) for k in {keys!r}}}))"
    runner = RunnerAdapter(ownership=_Ownership(), root=tmp_path / "runs", bundle_store=store)
    attempt = runner.start(
        replace(bundle, argv=(sys.executable, "-c", code)), _owner(), revision.spec.budget
    )
    try:
        finished = _finish(runner, attempt, _owner())
        assert finished.state is RunState.VALIDATING
        assert finished.process is not None and finished.process.thread_count == 1
        output = Path(finished.process.cwd) / "logs/solver.stdout.log"
        actual = json.loads(output.read_text())
        assert {key: actual[key] for key in expected} == expected
        assert actual["CAE_TEST_ENV_SENTINEL"] == "parent-value"
        assert {name: os.environ[name] for name in expected} == inherited
        assert not runner._managed
    finally:
        _cleanup(runner, attempt)


def test_bundle_worker_count_above_budget_is_rejected_before_spawn(tmp_path: Path) -> None:
    revision, _, _, bundle, store = _compiled(tmp_path)
    marker = tmp_path / "must-not-run"
    bundle = replace(
        bundle,
        thread_count=2,
        argv=(sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"),
    )
    runner = RunnerAdapter(ownership=_Ownership(), root=tmp_path / "runs", bundle_store=store)
    try:
        with pytest.raises(PortError) as caught:
            runner.start(bundle, _owner(), replace(revision.spec.budget, cpu_workers=1))
        assert caught.value.category is PortErrorCategory.INVALID_INPUT
        assert "worker" in str(caught.value)
        assert not marker.exists() and not runner._managed
    finally:
        for managed in list(runner._managed.values()):
            _cleanup(runner, managed.attempt)
