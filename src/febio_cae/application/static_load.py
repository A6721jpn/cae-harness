"""Explicit prepare/run/status application route for independent static loading."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import replace
from importlib import resources
from pathlib import Path
from typing import Any

from febio_cae.adapters.febio.compiler import LocalBundleStore
from febio_cae.adapters.febio.runner import RunnerAdapter
from febio_cae.adapters.febio.static_load import compile_static
from febio_cae.adapters.febio.xplt_reader import LocalResultDataStore, XpltReaderAdapter
from febio_cae.adapters.geometry.static_load import prepare_native
from febio_cae.domain import (
    Budget,
    CompatibilityProfile,
    FileEntry,
    FrameId,
    MeshArtifact,
    OutputMapping,
    PortError,
    PortErrorCategory,
    Quantity,
    ResolvedFileContent,
    RunState,
)
from febio_cae.domain.canonical import canonical_bytes
from febio_cae.domain.codec import decode_record
from febio_cae.domain.execution import AttemptRecord, ExecutionBundle
from febio_cae.domain.results import NumericResultData, ReadStatus, ResultManifest
from febio_cae.domain.static_load import StaticLoadRequest, integrate_edge_totals
from febio_cae.storage._ownership import pinned_read
from febio_cae.storage.static_load import StaticLoadStore

from ._profile_provisioning import _parse_bundle


def _read(path: Path, maximum: int) -> bytes:
    with pinned_read(path.absolute()) as stream:
        content = stream.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("static input/output exceeds declared size bound")
    return content


def _json(content: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise ValueError(f"nonfinite JSON constant: {value}")

    return json.loads(content, object_pairs_hook=pairs, parse_constant=constant)


def prepare(root: Path, cad: Path, request_file: Path) -> dict[str, Any]:
    request = StaticLoadRequest.from_dict(_json(_read(request_file, 1024 * 1024)))
    content = _read(cad, min(request.memory_bytes // 4, 256 * 1024 * 1024))
    if hashlib.sha256(content).hexdigest() != request.source_sha256:
        raise ValueError("original STEP SHA-256 differs from request")
    store = StaticLoadStore(root)
    with store.operation() as owned:
        if not owned:
            raise PortError(PortErrorCategory.CONFLICT, "static root is owned by another operation")
        if (store.root / "request.json").exists():
            raise PortError(
                PortErrorCategory.CONFLICT,
                "static root already contains a preparation; no implicit retry",
            )
        store.write_record("request.json", request.to_dict(), immutable=True)
        store.write_bytes("source.step", content, immutable=True)
        store.write_record(
            "preparation-status.json", {"status": "PREPARING", "request_digest": request.digest}
        )
        try:
            result = prepare_native(content, request, store.root / "native-preparation")
            if _read(cad, min(request.memory_bytes // 4, 256 * 1024 * 1024)) != content:
                raise PortError(
                    PortErrorCategory.INTEGRITY, "original source changed during owned preparation"
                )
            store.write_record("preparation.json", result, immutable=True)
            store.write_record("mesh.json", result["mesh"], immutable=True)
            store.write_record(
                "preparation-status.json",
                {
                    "status": "PREPARED",
                    "request_digest": request.digest,
                    "mesh_digest": result["mesh"]["artifact_digest"],
                },
            )
        except BaseException as error:
            store.write_record(
                "preparation-status.json",
                {"status": "FAILED", "request_digest": request.digest, "error": str(error)},
            )
            raise
        return store.read_record("preparation-status.json")


def _prepared(
    store: StaticLoadStore,
) -> tuple[StaticLoadRequest, MeshArtifact, dict[int, tuple[tuple[int, int, int], ...]]]:
    request = StaticLoadRequest.from_dict(store.read_record("request.json"))
    mesh = decode_record(canonical_bytes(store.read_record("mesh.json")), MeshArtifact)
    record = store.read_record("preparation.json")
    if (
        store.read_record("preparation-status.json")["status"] != "PREPARED"
        or hashlib.sha256(_read(store.root / "source.step", request.memory_bytes // 4)).hexdigest()
        != request.source_sha256
        or record["source_sha256"] != request.source_sha256
        or record["request_digest"] != request.digest
        or record["mesh"] != mesh.to_dict()
        or mesh.provenance.source_geometry_digest != record["inspection_geometry_digest"]
    ):
        raise PortError(
            PortErrorCategory.INTEGRITY, "static preparation lineage or source snapshot changed"
        )
    curves = {
        int(tag): tuple(tuple(line) for line in lines) for tag, lines in record["curves"].items()
    }
    fixed = {int(n) for s in mesh.sets if s.set_id.startswith("fixed-face-") for n in s.member_ids}
    forces = integrate_edge_totals(request, mesh, curves, fixed)
    if {str(n): list(f) for n, f in sorted(forces.items())} != record["nodal_forces_n"]:
        raise PortError(
            PortErrorCategory.INTEGRITY,
            "static prepared nodal forces differ from consistent edge integration",
        )
    return request, mesh, curves


def _profile() -> CompatibilityProfile:
    """Reuse qualified native XPLT identity, not planar-contact physics authority."""
    content = (
        resources.files("febio_cae.resources").joinpath("planar_default_bundle.json").read_bytes()
    )
    _, _, _, profiles, _ = _parse_bundle(content)
    original = profiles["outputs"]
    mappings = tuple(
        m for m in original.output_mappings if m.canonical_id in ("displacement", "reaction")
    ) + (
        OutputMapping(
            "stress", "stress", "element", "MAT3FS", "Pa", FrameId("World"), 1, 1, "value"
        ),
    )
    capabilities = tuple(c for c in original.capabilities if c.capability_id == "febio.output.xplt")
    return replace(
        original,
        profile_id="febio412-static-edge-xplt-v1",
        capabilities=capabilities,
        output_mappings=mappings,
    )


def _cross(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _summary(
    request: StaticLoadRequest,
    mesh: MeshArtifact,
    curves: dict[int, tuple[tuple[int, int, int], ...]],
    data: dict[str, Any],
) -> dict[str, Any]:
    fixed = {int(n) for s in mesh.sets if s.set_id.startswith("fixed-face-") for n in s.member_ids}
    forces = integrate_edge_totals(request, mesh, curves, fixed)
    coordinates = {n.node_id: n.coordinates_si for n in mesh.nodes}
    displacement = {
        int(n): tuple(data["displacement"].values[-1][3 * i : 3 * i + 3])
        for i, n in enumerate(data["displacement"].entity_ids)
    }
    reaction = {
        int(n): tuple(data["reaction"].values[-1][3 * i : 3 * i + 3])
        for i, n in enumerate(data["reaction"].entity_ids)
    }
    applied = tuple(math.fsum(f[a] for f in forces.values()) for a in range(3))
    support = tuple(math.fsum(reaction[n][a] for n in fixed) for a in range(3))
    residual = tuple(applied[a] + support[a] for a in range(3))
    moments: list[tuple[float, float, float]] = []
    for collection in (forces, {n: reaction[n] for n in fixed}):
        moments.extend(
            _cross(tuple(coordinates[n][a] + displacement[n][a] for a in range(3)), force)
            for n, force in collection.items()
        )
    moment_residual = tuple(math.fsum(m[a] for m in moments) for a in range(3))
    force_scale = math.fsum(
        math.sqrt(sum(v * v for v in load.total_force_n)) for load in request.loads
    )
    diameter = math.sqrt(
        sum(
            (max(p[a] for p in coordinates.values()) - min(p[a] for p in coordinates.values())) ** 2
            for a in range(3)
        )
    )
    force_tolerance = max(1e-6, 0.005 * force_scale)
    moment_tolerance = max(1e-9, 0.005 * force_scale * diameter)
    fixed_max = max(abs(v) for n in fixed for v in displacement[n])
    stress = data["stress"]
    components = stress.component_ids
    von_mises: list[float] = []
    for i in range(len(stress.entity_ids)):
        values = dict(
            zip(
                components,
                stress.values[-1][i * len(components) : (i + 1) * len(components)],
                strict=True,
            )
        )
        xx, yy, zz, xy, yz, xz = (values[k] for k in ("xx", "yy", "zz", "xy", "yz", "xz"))
        von_mises.append(
            math.sqrt(
                0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2)
                + 3 * (xy * xy + yz * yz + xz * xz)
            )
        )
    checks = {
        "force_equilibrium": "PASS" if max(abs(v) for v in residual) <= force_tolerance else "FAIL",
        "moment_equilibrium": "PASS"
        if max(abs(v) for v in moment_residual) <= moment_tolerance
        else "FAIL",
        "fixed_displacement": "PASS" if fixed_max <= 1e-12 else "FAIL",
        "finite_displacement_stress": "PASS",
    }
    return {
        "final_time_s": data["displacement"].axis_values[-1],
        "applied_force_n": list(applied),
        "support_reaction_n": list(support),
        "force_residual_n": list(residual),
        "moment_residual_nm": list(moment_residual),
        "force_tolerance_n": force_tolerance,
        "moment_tolerance_nm": moment_tolerance,
        "fixed_displacement_tolerance_m": 1e-12,
        "maximum_fixed_displacement_m": fixed_max,
        "maximum_displacement_m": max(
            math.sqrt(sum(v * v for v in row)) for row in displacement.values()
        ),
        "maximum_von_mises_pa": max(von_mises),
        "checks": checks,
        "quality_status": "FAIL" if "FAIL" in checks.values() else "UNVERIFIED",
        "unverified": [
            "arbitrary-CAD approximation",
            "mesh dependence",
            "solver residual qualification",
            "material yield/safety",
        ],
    }


def run(root: Path, solver: Path) -> dict[str, Any]:
    store = StaticLoadStore(root)
    with store.operation() as owned:
        if not owned:
            raise ValueError("static root is owned by another operation")
        request, mesh, curves = _prepared(store)
        profile = _profile()
        store.write_bytes("profile.json", profile.to_bytes(), immutable=True)
        bundle_store = LocalBundleStore(store.root / "bundles")
        bundle = compile_static(request, mesh, curves, profile, bundle_store, solver)
        owner = store.issue(bundle)
        try:
            budget = Budget(
                Quantity(request.solver_wall_seconds, "s"), 1, request.cpu_workers, 0, 0
            )
            runner = RunnerAdapter(
                ownership=store, root=store.root / "attempts", bundle_store=bundle_store
            )
            attempt = store.start(runner, bundle, owner, budget)
            while attempt.state in (RunState.RUNNING, RunState.DRAINING):
                time.sleep(0.02)
                attempt = store.poll(owner)
            if attempt.state is not RunState.VALIDATING or attempt.process is None:
                category = (
                    PortErrorCategory.CANCELLED
                    if attempt.state in (RunState.CANCELLED, RunState.INTERRUPTED)
                    else PortErrorCategory.EXECUTION
                )
                raise PortError(
                    category,
                    f"owned solver did not reach drained validation: {attempt.state.value}",
                )
            attempt_root = Path(attempt.process.cwd)
            log = _read(
                attempt_root / "output/solver.log", min(request.memory_bytes // 8, 8 * 1024 * 1024)
            )
            normal = b"N O R M A L   T E R M I N A T I O N"
            if log.count(normal) != 1 or b"E R R O R   T E R M I N A T I O N" in log:
                raise PortError(
                    PortErrorCategory.INTEGRITY,
                    "solver log does not prove unique normal termination",
                )
            store.write_bytes("sealed-solver.log", log, immutable=True)
            for name in ("stdout", "stderr"):
                store.write_bytes(
                    f"sealed-{name}.log",
                    _read(
                        attempt_root / f"logs/solver.{name}.log",
                        min(request.memory_bytes // 8, 8 * 1024 * 1024),
                    ),
                    immutable=True,
                )
            raw = _read(
                attempt_root / "output/results.xplt",
                min(request.memory_bytes // 4, 128 * 1024 * 1024),
            )
            store.write_bytes("sealed-results.xplt", raw, immutable=True)
            entry = FileEntry(
                "output/results.xplt", hashlib.sha256(raw).hexdigest(), len(raw), "result"
            )
            data_store = LocalResultDataStore()
            entity_ids = {
                m.canonical_id: tuple(str(e.element_id) for e in mesh.elements)
                if m.location == "element"
                else tuple(str(n.node_id) for n in mesh.nodes)
                for m in profile.output_mappings
            }
            data_store.register_source(
                attempt,
                bundle,
                ResolvedFileContent(entry, raw),
                mesh=mesh,
                state_times=tuple(i / 10 for i in range(11)),
                part_bodies={1: mesh.provenance.source_body_ids[0]},
                entity_ids=entity_ids,
            )
            manifest = XpltReaderAdapter(profile=profile, data_store=data_store).read(
                attempt, bundle
            )
            data = {
                m.canonical_id: data_store.resolve_manifest_output(
                    manifest.manifest_id, m.canonical_id
                )
                for m in profile.output_mappings
            }
            if any(output.axis_values[-1] != 1.0 for output in data.values()):
                raise PortError(
                    PortErrorCategory.INTEGRITY,
                    "XPLT final state does not prove the exact requested terminal time",
                )
            summary = _summary(request, mesh, curves, data)
            summary["terminal_log_sha256"] = hashlib.sha256(log).hexdigest()
            summary["xplt_sha256"] = entry.digest
            summary["xplt_path"] = str(store.root / "sealed-results.xplt")
            for name, output in data.items():
                store.write_bytes(f"numeric-{name}.json", output.to_bytes(), immutable=True)
            store.write_record("summary.json", summary, immutable=True)
            if summary["quality_status"] == "FAIL":
                raise PortError(
                    PortErrorCategory.QUALITY,
                    "static numerical boundary checks failed; sealed results retained",
                )
            _prepared(store)
            store.publish_manifest(owner, manifest)
            return {"status": "SUCCEEDED", "manifest_id": manifest.manifest_id, **summary}
        except BaseException as error:
            try:
                store.cancel_and_drain(owner)
            finally:
                store.fail(str(error))
            raise


def status(root: Path) -> dict[str, Any]:
    store = StaticLoadStore(root)
    with store.operation() as owned:
        result = store.read_record("preparation-status.json")
        if owned and result["status"] == "PREPARING":
            result = {
                **result,
                "status": "INTERRUPTED",
                "diagnostic": "No live preparation owner; no PID adoption or implicit retry",
            }
        if (store.root / "run.json").exists():
            run_record = store.read_record("run.json")
            result = {**result, "run": run_record}
            if run_record["status"] == "SUCCEEDED":
                request, mesh, curves = _prepared(store)
                bundle = decode_record(
                    canonical_bytes(store.read_record("bundle.json")), ExecutionBundle
                )
                manifest = decode_record(
                    canonical_bytes(store.read_record("manifest.json")), ResultManifest
                )
                profile = decode_record(
                    canonical_bytes(store.read_record("profile.json")), CompatibilityProfile
                )
                settings = {setting.name: setting.value for setting in bundle.settings}
                if (
                    bundle.spec_digest != request.digest
                    or bundle.mesh_digest != mesh.artifact_digest
                    or settings["compatibility_profile_digest"]
                    != hashlib.sha256(profile.to_bytes()).hexdigest()
                    or manifest.bundle_digest != bundle.bundle_digest
                    or manifest.attempt_id != run_record["owner"]["attempt_id"]
                ):
                    raise PortError(
                        PortErrorCategory.INTEGRITY, "stored static publication lineage changed"
                    )
                attempt = decode_record(canonical_bytes(run_record["attempt"]), AttemptRecord)
                owner_scope = {
                    "case_id": attempt.case_id,
                    "run_id": attempt.run_id,
                    "attempt_id": attempt.attempt_id,
                    "owner_generation": attempt.owner_generation,
                }
                process = attempt.process
                expected_cwd = (
                    store.root
                    / "attempts"
                    / attempt.case_id
                    / attempt.run_id
                    / attempt.attempt_id
                    / str(attempt.owner_generation)
                ).resolve()
                if (
                    run_record["claimed"] is not True
                    or run_record["owner"] != owner_scope
                    or run_record["bundle_digest"] != bundle.bundle_digest
                    or attempt.state is not RunState.SUCCEEDED
                    or attempt.case_id != bundle.case_id
                    or attempt.revision_id != bundle.revision_id
                    or attempt.bundle_digest != bundle.bundle_digest
                    or process is None
                ):
                    raise PortError(
                        PortErrorCategory.INTEGRITY,
                        "stored successful static attempt ownership changed",
                    )
                if (
                    process.executable != str(Path(bundle.argv[0]).resolve())
                    or process.executable_digest != bundle.tool.executable_digest
                    or tuple(process.argv) != tuple(bundle.argv)
                    or process.thread_count != bundle.thread_count
                    or process.cwd != str(expected_cwd)
                ):
                    raise PortError(
                        PortErrorCategory.INTEGRITY,
                        "stored successful static process binding changed",
                    )
                if (
                    profile.to_bytes() != _profile().to_bytes()
                    or bundle.tool != profile.solver
                    or bundle.profile_id != profile.profile_id
                    or manifest.read_result.status is not ReadStatus.VALIDATED
                    or manifest.read_result.reader != profile.reader
                ):
                    raise PortError(
                        PortErrorCategory.INTEGRITY, "stored static reader qualification changed"
                    )
                raw = _read(
                    store.root / "sealed-results.xplt",
                    min(request.memory_bytes // 4, 128 * 1024 * 1024),
                )
                if (
                    len(manifest.files) != 1
                    or manifest.files[0].logical_path != "output/results.xplt"
                    or manifest.files[0].role != "result"
                    or manifest.files[0].digest != hashlib.sha256(raw).hexdigest()
                    or manifest.files[0].size_bytes != len(raw)
                ):
                    raise PortError(PortErrorCategory.INTEGRITY, "sealed static XPLT changed")
                data = {}
                for observation in manifest.read_result.observations:
                    numeric = decode_record(
                        canonical_bytes(store.read_record(f"numeric-{observation.output_id}.json")),
                        NumericResultData,
                    )
                    numeric.verify_content_digest()
                    if numeric.reference != observation.data_ref:
                        raise PortError(
                            PortErrorCategory.INTEGRITY, "sealed static numerical output changed"
                        )
                    data[observation.output_id] = numeric
                expected = _summary(request, mesh, curves, data)
                recorded = store.read_record("summary.json")
                if any(recorded[key] != value for key, value in expected.items()):
                    raise ValueError("stored static numerical assessment changed")
                log = _read(
                    store.root / "sealed-solver.log",
                    min(request.memory_bytes // 8, 8 * 1024 * 1024),
                )
                if (
                    hashlib.sha256(log).hexdigest() != recorded["terminal_log_sha256"]
                    or recorded["xplt_sha256"] != manifest.files[0].digest
                ):
                    raise ValueError("static terminal evidence changed")
            if owned and run_record["status"] in (
                "CREATED",
                "PREPARING",
                "RUNNING",
                "DRAINING",
                "VALIDATING",
            ):
                result["run"] = {
                    **run_record,
                    "status": "INTERRUPTED",
                    "diagnostic": "No live local process owner; no PID adoption or implicit retry",
                }
        if (store.root / "summary.json").exists():
            result["summary"] = store.read_record("summary.json")
        return result
