"""Synthetic registered public reports; never native solver or grammar qualification."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import test_comparison as comparison
import test_preview_storage as previews

from febio_cae.application import _demo
from febio_cae.application._preview import preview_summary
from febio_cae.domain import ExecutionBundle, FileEntry, Quantity, SolverControl
from febio_cae.domain.ports import PortError, PortErrorCategory


@pytest.fixture
def tmp_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("n")


def _log(mode: str) -> bytes:
    fields = {
        "Module type": "solid",
        "analysis": "STATIC (0)",
        "time_steps": "10",
        "step_size": "0.1",
        "Auto time stepper activated": "no",
        "dtol": "0.001",
        "etol": "0.01",
        "rtol": "0.001",
        "min_residual": "1e-20" if mode == "shortcut" else "0",
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
        "  version 4.12.0",
        *(f"\t{k} .... : {v}" for k, v in fields.items()),
        "contact interface 1 - Type: sliding-elastic",
    ]
    for step in range(1, 11):
        if mode == "contrary" and step == 10:
            continue  # unrelated missing coverage cannot hide step9's trusted final FAIL
        t = str(step / 10) if step < 10 else "1"
        lines.append(f"===== beginning time step {step} : {t} =====")
        count = 11 if step == 1 else 2
        for iteration in range(1, count + 1):
            final = iteration == count
            current = "1.000000e-04" if final else "2.000000e+00"
            required = "1.000000e-03" if final else "1.000000e-02"
            if mode == "equal" and final and step == 1:
                current = required
            lines += [
                f" {iteration}",
                f" Nonlinear solution status: time= {t}",
                "\tstiffness updates = 1",
                "\tright hand side evaluations = 3",
                "\tstiffness matrix reformations = 1",
                "\tstep from line search = 1.000000",
                "\tconvergence norms : INITIAL CURRENT REQUIRED",
            ]
            lines += [
                f"\t   {name} 1.000000e+00 {current} {required} "
                for name in ("residual", "energy", "displacement")
            ]
            if step == 1 and iteration == 10:
                lines += ["Max nr of iterations reached.", "Stiffness matrix will now be reformed."]
        gap = "2.000000e-08" if mode == "contrary" and step == 9 else "1.000000e-09"
        lines += [
            "........................ augmentation # 1",
            " sliding interface # 1",
            "                        CURRENT REQUIRED",
            "    D multiplier : 1.000000e-03 1.000000e-02",
            f"    maximum gap : {gap} 1.000000e-08",
            "convergence summary",
            f"    number of iterations : {count}",
            "    number of reformations : 0",
            f"------- converged at time : {t}",
        ]
        if mode == "unsupported" and step == 1:
            lines.insert(-1, "retrying time step with cutback")
    lines += ["N O R M A L   T E R M I N A T I O N"]
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


def _input(revision: Any) -> bytes:
    controls = {c.name: c.value for c in revision.spec.solver_policy.controls}

    def value(name: str) -> str:
        v = controls[name]
        return f"{v.to_si().value:.17g}" if isinstance(v, Quantity) else str(int(v))

    solver = "".join(
        f"<{k}>{value(k)}</{k}>"
        for k in ("dtol", "etol", "rtol", "min_residual", "max_refs", "reform_augment")
    )
    contact = "".join(
        f"<{k}>{value(k)}</{k}>"
        for k in ("laugon", "tolerance", "gaptol", "minaug", "maxaug", "two_pass")
    )
    return (
        f'<febio_spec version="4.0"><Module type="solid"/><Control><analysis type="static"/>'
        f'<time_steps>10</time_steps><step_size>0.10000000000000001</step_size><solver type="solid">{solver}'
        f'<qn_method type="BFGS"><max_ups>10</max_ups></qn_method></solver></Control>'
        f'<Contact><contact type="sliding-elastic">{contact}</contact></Contact></febio_spec>'
    ).encode()


def _public(
    tmp_path: Path, patch: pytest.MonkeyPatch, mode: str
) -> tuple[Any, Any, list[dict[str, Any]]]:
    original_configure = comparison._configure
    original_demo = _demo.run_demo
    captured: dict[str, Any] = {}

    def configure(service: Any, spec: Any) -> Any:
        spec = original_configure(service, spec)
        profile = service.compatibility.get_profile(spec.solver_policy.profile.profile_id)
        profile = replace(
            profile, profile_id="pn-synthetic", solver=replace(profile.solver, version="4.12.0")
        )
        service.register_profile(profile)
        digest = hashlib.sha256(profile.to_bytes()).hexdigest()
        spec = replace(
            spec,
            **{
                n: replace(
                    getattr(spec, n),
                    profile=replace(
                        getattr(spec, n).profile,
                        profile_id=profile.profile_id,
                        record_digest=digest,
                    ),
                )
                for n in ("solver_policy", "outputs", "quality_policy")
            },
        )
        additions = {
            "dtol": Quantity(0.001, "1"),
            "etol": Quantity(0.01, "1"),
            "rtol": Quantity(0.001, "1"),
            "min_residual": Quantity(1e-20 if mode == "shortcut" else 0, "1"),
            "max_refs": 50,
            "reform_augment": True,
            "max_ups": 10,
            "minaug": 0,
            "maxaug": 10,
            "laugon": 1,
            "tolerance": Quantity(0.01, "1"),
            "gaptol": Quantity(1e-8, "m"),
            "two_pass": False,
        }
        controls = {c.name: c.value for c in spec.solver_policy.controls} | additions
        spec = replace(
            spec,
            solver_policy=replace(
                spec.solver_policy,
                controls=tuple(SolverControl(k, v) for k, v in controls.items()),
                increments=replace(
                    spec.solver_policy.increments,
                    initial_step=Quantity(0.1, "s"),
                    minimum_step=Quantity(0.1, "s"),
                    maximum_step=Quantity(0.1, "s"),
                    adaptive=False,
                    max_steps=10,
                    max_step_retries=0,
                ),
            ),
        )
        captured["xml"] = _input(SimpleRevision(spec))
        return spec

    def bundle(*args: Any, **kwargs: Any) -> ExecutionBundle:
        original = ExecutionBundle(*args, **kwargs)
        payload = captured["xml"]
        entry = FileEntry(
            "input/case.feb", hashlib.sha256(payload).hexdigest(), len(payload), "input"
        )
        return replace(
            original,
            files=(entry,),
            argv=(
                original.argv[0],
                "-i",
                entry.logical_path,
                "-o",
                "output/solver.log",
                "-p",
                "output/results.xplt",
                "-noappend",
                "-noconfig",
            ),
        )

    def demo_fixture(service: Any, storage: Any, revision: Any, path: Path, inner: Any) -> Any:
        captured["service"] = service
        register, seal = storage._register_execution, storage._seal_native_output

        def register_input(owner: Any, b: Any, mesh: Any, profile: Any, inputs: Any) -> Any:
            return register(owner, b, mesh, profile, {"input/case.feb": captured["xml"]})

        def seal_log(owner: Any) -> Any:
            root = Path(storage._native_context(storage._attempt(owner))["process_root"])
            log_path = root / "output/solver.log"
            if mode == "missing":
                pass
            else:
                log_path.write_bytes(_log(mode))
            return seal(owner)

        inner.setattr(storage, "_register_execution", register_input)
        inner.setattr(storage, "_seal_native_output", seal_log)
        from types import SimpleNamespace

        original_read, original_register = storage._read_candidate, storage.register_numeric_data
        numeric: dict[str, Any] = {}
        context: dict[str, Any] = {}
        storage.ingest_source(
            asset_id="registered-reader-source",
            source_kind="registered_document",
            media_type="text/plain",
            content=Path(_demo.xplt_reader.__file__).read_bytes(),
        )

        def record(data: Any) -> Any:
            numeric[data.reference.data_id] = data
            return original_register(data)

        def resolve(reference: Any) -> Any:
            data = numeric[reference.data_id]
            assert data.reference == reference
            return data

        class Reader:
            def __init__(self, **kwargs: Any) -> None:
                pass

            def read(self, attempt: Any, bundle: Any) -> Any:
                _, lineage = storage._lineage(attempt)
                xplt = tuple(e for e in storage._sealed_entries(lineage) if e.role == "result")
                return context["reader"](attempt, bundle, xplt, storage)

        def execute(case_id: str, revision_id: str, **kwargs: Any) -> Any:
            def read(owner: Any, synthetic: Any) -> Any:
                context["reader"] = synthetic
                return original_read(owner, kwargs["read"])

            inner.setattr(storage, "_read_candidate", read)
            return comparison._result(service, storage, revision, "preview", (0, 0.5, 1), 1)

        # Real demo/read/publication; only external runner and XPLT payload production are synthetic.
        inner.setattr(storage, "register_numeric_data", record)
        inner.setattr(service, "_execute_ports", execute)
        inner.setattr(_demo, "XpltReaderAdapter", Reader)
        inner.setattr(
            _demo,
            "LocalResultDataStore",
            lambda: SimpleNamespace(register_source=lambda *a, **k: None, resolve=resolve),
        )
        result = _demo.run_demo(
            service,
            revision.case_id,
            revision.revision_id,
            executable=str(path / "synthetic-solver"),
            preflight=False,
        )
        return storage.get_manifest(cast(dict[str, Any], result["manifest"])["manifest_id"])

    def demo(*args: Any, **kwargs: Any) -> Any:
        response = original_demo(*args, **kwargs)
        captured["demo"] = response
        return response

    patch.setattr(comparison, "_configure", configure)
    patch.setattr(comparison, "ExecutionBundle", bundle)
    patch.setattr(previews, "_demo_log_result", demo_fixture)
    patch.setattr(_demo, "run_demo", demo)
    store, preview_id, _ = previews._quality_preview(tmp_path, mode="solver_log")
    target = store.target(store.get(preview_id)["receipt"]["manifest_id"])
    responses = [
        captured["demo"],
        preview_summary(store, preview_id),
        captured["service"].run_status(target.attempt.case_id, target.attempt.run_id),
    ]
    return store, target, responses


class SimpleRevision:
    def __init__(self, spec: Any) -> None:
        self.spec = spec


def test_reported_solver_norms_public_final_cycles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, target, responses = _public(tmp_path, monkeypatch, "pass")
    for response in responses:
        report = response["required_quality"]["reported_solver_norms"]
        assert report["final_status"] == "PASS"
        assert report["native_qualification"] == "UNVERIFIED"
        assert len(report["accepted_steps"]) == 10  # fixture XPLT has only three saved states
        assert (
            report["bindings"]["manifest_digest"]
            == hashlib.sha256(target.manifest.to_bytes()).hexdigest()
        )
        assert (
            response["quality_status"] == "UNVERIFIED"
            and response["task_status"] == "NEEDS_QUALITY"
        )
        assert (
            next(
                r
                for r in response["required_quality"]["numerical"]
                if r["criterion_id"] == "solver_residual"
            )["status"]
            == "UNVERIFIED"
        )
        assert "Stiffness matrix will now be reformed." in str(report["blocks"])
        row = next(b for b in report["blocks"] if b["kind"] == "nonlinear")["rows"][0]
        assert row["current"] == "2.000000e+00" and row["required"] == "1.000000e-02"
        assert _log("pass")[slice(*row["current_span"])] == row["current"].encode()


def test_reported_solver_norms_final_fail_survives_missing_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, responses = _public(tmp_path, monkeypatch, "contrary")
    for response in responses:
        assert response["required_quality"]["reported_solver_norms"]["final_status"] == "FAIL"
        assert response["quality_status"] == "FAIL" and response["task_status"] == "FAILED"
        assert response["run_status"] == "SUCCEEDED"


@pytest.mark.parametrize("mode", ["equal", "shortcut", "unsupported", "missing"])
def test_reported_solver_norms_unverified_and_integrity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    store, target, responses = _public(tmp_path, monkeypatch, mode)
    for response in responses:
        report = response["required_quality"]["reported_solver_norms"]
        assert report["final_status"] == "UNVERIFIED"
        assert report["reasons"]
    if mode == "equal":
        log = next(e for e in target.manifest.files if e.role == "solver_log")
        path = (
            store.storage.root
            / f"cases/{target.attempt.case_id}/runs/{target.attempt.run_id}/attempts/{target.attempt.attempt_id}/{log.logical_path}"
        )
        path.write_bytes(b"changed")
        with pytest.raises(PortError) as error:
            store.storage.get_manifest(target.manifest.manifest_id)
        assert error.value.category is PortErrorCategory.INTEGRITY
