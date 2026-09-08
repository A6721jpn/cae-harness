from __future__ import annotations

import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from febio_cae.adapters.febio._windows_job import WindowsJobProcess
from febio_cae.adapters.preview import studio as preview_module
from febio_cae.domain import (
    AssessmentStatus,
    CoulombFriction,
    NumericResultData,
    PortError,
    PreviewStatus,
    Quantity,
    RunState,
    ToolIdentity,
)
from febio_cae.domain.codec import decode_record, encode_record

from .fixtures import evidence
from .mixed_fixture import MixedPreviewCase
from .reader_fixture import setup_reader
from .runner_fixture import _compiled, _owner, _Ownership
from .test_compiler_native import _required
from .test_quality_numeric import assess, controlled_case
from .test_runner_job import _cleanup, _hold_child, _until


def test_compiler_uses_native_febio_structure_and_semantics(tmp_path: Path) -> None:
    revision, mesh, _profile, bundle, store = _compiled(tmp_path)
    content = store.resolve(bundle, "input/case.feb")

    assert b'<febio_spec version="4.0">' in content
    assert b"<MeshDomains>" in content
    assert b"<SolidDomain" in content
    root = ET.fromstring(content)
    groups = root.findall("Mesh/Nodes")
    assert len(groups) == 2
    assert all("name" not in group.attrib for group in groups)
    assert [{int(n.attrib["id"]) for n in group} for group in groups] == [
        set(range(1, 11)),
        set(range(11, 21)),
    ]
    elements = root.findall("Mesh/Elements")
    assert {g.attrib["name"] for g in elements} == {
        "compiled-part-elements",
        "compiled-tool-elements",
    }
    assert all(g.attrib["type"] == "tet10" for g in elements)
    assert {int(e.attrib["id"]) for g in elements for e in g} == {1, 2}
    plot = _required(root, "Output/plotfile")
    assert plot.attrib["type"] == "febio"
    assert {v.attrib["type"] for v in plot.findall("var")} == {"displacement", "rigid force"}
    assert all(not (v.text or "").strip() for v in plot.findall("var"))
    # Nondegenerate fixture coordinates include -0.01: reject negative zero
    # tokens, not legitimate negative coordinates sharing that byte prefix.
    assert re.search(rb"(?<![\d.])-0(?:\.0+)?(?:[eE][+-]?\d+)?(?=[,<\s])", content) is None
    assert revision.spec.motion.direction.z == 1.0
    assert mesh.artifact_digest == bundle.mesh_digest


def test_compiler_preserves_signed_motion_contact_numbers_and_resolved_set_ids(
    tmp_path: Path,
) -> None:
    revision, mesh, profile, _bundle, _store = _compiled(tmp_path)
    from febio_cae.adapters.febio.compiler import CompilerAdapter, LocalBundleStore

    friction = CoulombFriction(
        coefficient=Quantity(0.37, "1"),
        model_evidence=evidence("contact.friction_model", "r1-friction-model"),
        coefficient_evidence=evidence("contact.friction_coefficient", "r1-friction-coefficient"),
    )
    changed_contact = replace(revision.spec.contact, friction=friction)
    changed_motion = replace(
        revision.spec.motion,
        direction=replace(revision.spec.motion.direction, z=-1.0),
    )
    changed_revision = replace(
        revision,
        spec=replace(revision.spec, contact=changed_contact, motion=changed_motion),
    )
    resolved_sets = tuple(replace(item, set_id=f"resolved-{item.set_id}") for item in mesh.sets)
    resolved_mesh = replace(mesh, sets=resolved_sets)
    store = LocalBundleStore(tmp_path / "bundles-r1")
    bundle = CompilerAdapter(store=store, executable=sys.executable).compile(
        changed_revision, resolved_mesh, profile
    )
    content = store.resolve(bundle, "input/case.feb")

    root = ET.fromstring(content)
    prescribed = _required(root, "Rigid/rigid_bc[@type='rigid_displacement']")
    assert prescribed.findtext("dof") == "z"
    value = _required(prescribed, "value")
    curve = _required(root, f"LoadData/load_controller[@id='{value.attrib['lc']}']")
    points = [
        tuple(float(x) for x in (p.text or "").split(",")) for p in curve.findall("points/point")
    ]
    assert points == [(0.0, 0.0), (1.0, 0.0001)]
    assert float(value.text or "nan") * points[-1][1] == pytest.approx(-0.0001)
    assert float(root.findtext("Contact/contact/fric_coeff", "nan")) == pytest.approx(0.37)
    pair = _required(root, "Mesh/SurfacePair")
    assert pair.findtext("primary") == "resolved-tool-contact"
    assert pair.findtext("secondary") == "resolved-part-contact"
    assert b"resolved-tool-contact" in content
    assert b"resolved-part-contact" in content
    assert b">explicit-profile<" not in content


def test_reader_accepts_observed_header_without_private_identity_and_codec_round_trips(
    tmp_path: Path,
) -> None:
    reader, attempt, bundle, mesh, payload = setup_reader(tmp_path)
    assert attempt.attempt_id.encode() not in payload
    assert bundle.bundle_digest.encode() not in payload
    assert mesh.artifact_digest.encode() not in payload
    manifest = reader.read(attempt, bundle)
    numeric = reader.data_store.resolve_manifest_output(manifest.manifest_id, "displacement")
    assert numeric.reference.codec_id == "numeric-result-v1"
    assert decode_record(encode_record(numeric), NumericResultData) == numeric


def test_reader_rejects_unknown_state_block_and_allows_one_state(tmp_path: Path) -> None:
    reader, attempt, bundle, _, _ = setup_reader(tmp_path, defect="unknown-state")
    with pytest.raises(PortError, match="state"):
        reader.read(attempt, bundle)


def test_reader_accepts_a_single_observed_state(tmp_path: Path) -> None:
    reader, attempt, bundle, _, _ = setup_reader(tmp_path, times=(0.0,))
    manifest = reader.read(attempt, bundle)
    assert all(item.state_count == 1 for item in manifest.read_result.observations)
    assert reader.data_store.resolve_manifest_output(
        manifest.manifest_id, "displacement"
    ).axis_values == (0.0,)


def test_quality_selects_requested_component_and_rejects_wrong_binding(tmp_path: Path) -> None:
    case = controlled_case(tmp_path)
    assessment = assess(case)
    assert assessment.overall_status is AssessmentStatus.PASS
    assert assessment.criteria[0].measured[0].value == pytest.approx(0.2)
    numeric = case.numeric()
    assert numeric.values[0][0] == 3.0
    case.replace_numeric(
        replace(
            numeric,
            reference=replace(
                numeric.reference, bundle_digest="9" * 64, attempt_id="other-attempt"
            ),
        )
    )
    # A correctly rehashed public codec record still fails its execution binding.
    assert case.numeric().reference.content_digest == case.numeric().expected_content_digest
    rejected = assess(case)
    assert rejected.overall_status is AssessmentStatus.UNVERIFIED
    assert not rejected.criteria[0].measured


def test_runner_rejects_path_escape(tmp_path: Path) -> None:
    revision, _mesh, _profile, bundle, _store = _compiled(tmp_path)
    from febio_cae.adapters.febio.runner import RunnerAdapter

    runner = RunnerAdapter(ownership=_Ownership(), root=tmp_path / "runs")
    with pytest.raises(PortError, match="run_id"):
        runner.start(bundle, _owner("../escaped"), revision.spec.budget)


def test_runner_rejects_unregistered_executable_before_spawn(tmp_path: Path) -> None:
    revision, _mesh, _profile, bundle, _store = _compiled(tmp_path)
    from febio_cae.adapters.febio.runner import RunnerAdapter

    runner = RunnerAdapter(ownership=_Ownership(), root=tmp_path / "runs")
    marker = tmp_path / "ran.txt"
    unregistered = replace(
        bundle,
        tool=replace(bundle.tool, executable_digest="0" * 64),
        argv=(
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
        ),
    )
    with pytest.raises(PortError, match="executable"):
        runner.start(unregistered, _owner(), revision.spec.budget)
    time.sleep(0.05)
    assert not marker.exists()


def test_runner_tracks_real_owned_writer_through_uncertain_enumeration_and_natural_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    revision, _mesh, _profile, bundle, _store = _compiled(tmp_path)
    from febio_cae.adapters.febio.runner import RunnerAdapter

    runner = RunnerAdapter(ownership=_Ownership(), root=tmp_path / "runs")
    child_code = (
        "from pathlib import Path\n"
        "import time\n"
        "path = Path('output/owned-writer.bin')\n"
        "for _ in range(20):\n"
        "    with path.open('ab') as stream: stream.write(b'x' * 256); stream.flush()\n"
        "    time.sleep(0.05)\n"
    ).strip()
    root_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(0.35)"
    ).strip()
    bundle = replace(bundle, argv=(sys.executable, "-c", root_code))
    started = runner.start(bundle, _owner(), revision.spec.budget)
    managed = next(iter(runner._managed.values()))
    process = managed.process
    assert isinstance(process, WindowsJobProcess)
    try:
        _until(lambda: process.poll() == 0)
        output = managed.attempt_root / "output/owned-writer.bin"
        _until(output.exists)
        assert process.active_processes() >= 1

        def unavailable(self: Any) -> int:
            raise OSError("injected job accounting unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(type(process), "active_processes", unavailable)
            draining = runner.poll(started, _owner()).attempt
            assert draining.state is RunState.DRAINING
            before = output.stat().st_size
            _until(lambda: output.stat().st_size > before)
            assert runner.reconcile(draining, _owner()).attempt.state is RunState.DRAINING
        _until(lambda: process.active_processes() == 0)
        reconciled = runner.reconcile(draining, _owner()).attempt
        assert reconciled.state is RunState.VALIDATING
        assert process.closed and not runner._managed
    finally:
        _cleanup(runner, started)


def test_runner_cancel_drains_owned_tree_without_killing_unowned_process(tmp_path: Path) -> None:
    revision, _mesh, _profile, bundle, _store = _compiled(tmp_path)
    from febio_cae.adapters.febio.runner import RunnerAdapter

    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    long_runner = RunnerAdapter(ownership=_Ownership(), root=tmp_path / "runs-long")
    child_code = "from pathlib import Path;import os,time;Path('output/child.pid').write_text(str(os.getpid()));time.sleep(10)"
    root_code = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(10)"
    ).strip()
    long_bundle = replace(bundle, argv=(sys.executable, "-c", root_code))
    child_handle = None
    long_started = None
    try:
        long_started = long_runner.start(long_bundle, _owner(), revision.spec.budget)
        managed = next(iter(long_runner._managed.values()))
        process = managed.process
        assert isinstance(process, WindowsJobProcess)
        child_handle = _hold_child(process, managed.attempt_root / "output/child.pid")
        assert process.active_processes() >= 2
        long_started = long_runner.poll(long_started, _owner()).attempt
        cancelled = long_runner.cancel(long_started, _owner()).attempt
        assert cancelled.state is RunState.CANCELLED
        assert unrelated.poll() is None
        assert process.closed and not long_runner._managed
        assert process._win.WaitForSingleObject(child_handle, 0) == 0
    finally:
        _cleanup(long_runner, long_started)
        if child_handle is not None:
            process._win.CloseHandle(child_handle)
        if unrelated.poll() is None:
            unrelated.terminate()
            unrelated.wait(timeout=3)


def test_preview_requires_configured_launcher(tmp_path: Path) -> None:
    case = MixedPreviewCase(tmp_path)
    adapter = case.adapter(launcher=None)
    assert adapter.request(case.manifest, case.request).status is PreviewStatus.FAILED
    assert not case.launched and case.observed == 0


def test_preview_rejects_foreign_studio_receipt(tmp_path: Path) -> None:
    case = MixedPreviewCase(tmp_path)
    adapter = case.adapter()
    receipt = adapter.request(case.manifest, case.request)
    assert receipt.status is PreviewStatus.LAUNCHED
    wrong_studio = replace(receipt, studio=ToolIdentity("febio-studio", "99.0.0", "0" * 64))
    assert adapter.confirm(wrong_studio, case.evidence).status is PreviewStatus.FAILED
    assert case.observed == 0


def test_preview_rejects_mutation_during_confirmation(tmp_path: Path) -> None:
    case = MixedPreviewCase(tmp_path)

    def mutate(binding: preview_module.PreviewBinding) -> preview_module.PreviewObservation:
        case.path.write_bytes(case.path.read_bytes() + b"mutation")
        return case.observe(binding)

    adapter = case.adapter(observer=mutate)
    receipt = adapter.request(case.manifest, case.request)
    assert receipt.status is PreviewStatus.LAUNCHED
    confirmed = adapter.confirm(receipt, case.evidence)
    assert confirmed.status is PreviewStatus.FAILED
    assert case.observed == 1
