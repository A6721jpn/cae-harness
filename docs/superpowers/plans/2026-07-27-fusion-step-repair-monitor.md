# Fusion STEP Repair Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and locally verify a Windows Autodesk Fusion add-in that detects suspicious B-Rep faces, monitors Fusion's standard face-delete operation, exports before-and-after STEP files, compares them in a self-contained OpenCascade process, and displays PASS, WARNING, FAIL, or ERROR.

**Architecture:** A Fusion Python 3.14 add-in owns selection, highlighting, STEP export, monitoring, and a persistent HTML palette. A separate CPython 3.12/OpenCascade executable performs deterministic geometry analysis and communicates only through versioned UTF-8 JSON files. Pure-Python domain logic is shared by the comparator and copied into the installed add-in.

**Tech Stack:** Autodesk Fusion Python API; Python 3.14-compatible standard library for add-in code; CPython 3.12.13; `cadquery-ocp==7.9.3.1.1`; `PyInstaller==6.21.0`; `pytest==9.1.1`; HTML/CSS/vanilla JavaScript; PowerShell build and install scripts.

## Global Constraints

- Target platform is Windows and the locally installed Autodesk Fusion build.
- Initial scope is exactly one selected component containing exactly one closed B-Rep solid.
- The add-in detects and highlights candidates but never deletes or heals geometry.
- Repair is performed only with Fusion's standard face-delete/heal operation.
- No code path automatically invokes Undo or promotes a comparison baseline.
- PASS promotion is direct; WARNING promotion requires confirmation; FAIL and ERROR cannot be promoted.
- Default limits are volume delta `0.01%`, per-axis bounding-box delta `0.01 mm`, symmetric-difference volume `0.02%`, and warning boundary `80%`.
- Automatic detection uses `short_edge_mm = clamp(D × 1e-4, 0.001, 0.05)`, `small_face_mm2 = clamp(D² × 2e-5, 0.0001, 1.0)`, and compactness `0.01`.
- Comparison and session artifacts stay under `%LOCALAPPDATA%\BRepSliverMonitor\sessions`.
- External comparison is offline and requires no system Python installation.
- External comparison timeout defaults to `120 seconds`.
- Existing Bottom Frame STEP and mesh/report artifacts are user data and must not be modified or committed.

---

## File Structure

```text
fusion_step_repair_monitor/
  pyproject.toml
  requirements-build.txt
  README.md
  src/
    brep_monitor_core/
      __init__.py
      models.py
      detection.py
      classification.py
      schema.py
    step_compare/
      __init__.py
      __main__.py
      cli.py
      occ_analysis.py
  addin/
    FusionStepRepairMonitor/
      FusionStepRepairMonitor.py
      FusionStepRepairMonitor.manifest
      config.py
      monitor/
        __init__.py
        app.py
        fusion_gateway.py
        candidate_service.py
        session_store.py
        comparison_runner.py
        palette_controller.py
      lib/
        brep_monitor_core/
      palette/
        index.html
        styles.css
        app.js
      bin/
        step_compare/
  scripts/
    bootstrap.ps1
    build_comparator.ps1
    stage_addin.ps1
    install_addin.ps1
    verify_distribution.ps1
  tests/
    unit/
      test_detection.py
      test_classification.py
      test_schema.py
      test_session_store.py
      test_comparison_runner.py
    integration/
      occ_fixtures.py
      test_occ_analysis.py
      test_cli.py
    regression/
      test_bottom_frame.py
    fusion_stubs/
      adsk/
        __init__.py
        core.py
        fusion.py
```

`src/brep_monitor_core` is the canonical shared source. `stage_addin.ps1`
copies it into `addin/FusionStepRepairMonitor/lib/brep_monitor_core`; the
copied directory is a generated distribution artifact and is not edited
directly.

---

### Task 1: Core models, automatic thresholds, and candidate scoring

**Files:**
- Create: `fusion_step_repair_monitor/pyproject.toml`
- Create: `fusion_step_repair_monitor/requirements-build.txt`
- Create: `fusion_step_repair_monitor/src/brep_monitor_core/__init__.py`
- Create: `fusion_step_repair_monitor/src/brep_monitor_core/models.py`
- Create: `fusion_step_repair_monitor/src/brep_monitor_core/detection.py`
- Create: `fusion_step_repair_monitor/tests/unit/test_detection.py`
- Create: `fusion_step_repair_monitor/scripts/bootstrap.ps1`

**Interfaces:**
- Produces: `DetectionThresholds`, `ComparisonThresholds`, `ShapeMetrics`,
  `CandidateMetrics`, `Classification`, `Decision`, and `Reason` dataclasses
  and enums.
- Produces: `auto_detection_thresholds(diagonal_mm: float) ->
  DetectionThresholds`.
- Produces: `face_compactness(area_mm2: float, perimeter_mm: float) -> float`.
- Produces: `candidate_metrics(face_token: str, area_mm2: float,
  perimeter_mm: float, min_edge_mm: float, thresholds:
  DetectionThresholds) -> CandidateMetrics | None`.

- [ ] **Step 1: Write the failing detection tests**

```python
from brep_monitor_core.detection import (
    auto_detection_thresholds,
    candidate_metrics,
    face_compactness,
)


def test_auto_thresholds_for_100_mm_body():
    value = auto_detection_thresholds(100.0)
    assert value.short_edge_mm == 0.01
    assert value.small_face_mm2 == 0.2
    assert value.sliver_compactness == 0.01


def test_auto_thresholds_are_clamped():
    assert auto_detection_thresholds(0.001).short_edge_mm == 0.001
    assert auto_detection_thresholds(10_000).short_edge_mm == 0.05
    assert auto_detection_thresholds(10_000).small_face_mm2 == 1.0


def test_compactness_and_severity_classify_short_edge():
    thresholds = auto_detection_thresholds(100.0)
    item = candidate_metrics(
        face_token="face-1",
        area_mm2=10.0,
        perimeter_mm=20.0,
        min_edge_mm=0.005,
        thresholds=thresholds,
    )
    assert item is not None
    assert item.categories == ("short_edge",)
    assert item.severity == 2.0


def test_ordinary_face_is_not_candidate():
    thresholds = auto_detection_thresholds(100.0)
    assert candidate_metrics("face-2", 100.0, 40.0, 10.0, thresholds) is None
```

- [ ] **Step 2: Bootstrap the Python 3.12 development environment**

Create `requirements-build.txt` with exact pins:

```text
cadquery-ocp==7.9.3.1.1
pyinstaller==6.21.0
pytest==9.1.1
```

Create `bootstrap.ps1` accepting a mandatory `-Python` path, creating
`.venv`, and installing `-e .` plus `requirements-build.txt`.

Run:

```powershell
.\scripts\bootstrap.ps1 -Python "C:\Users\backo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
```

Expected: `.venv\Scripts\python.exe` reports Python 3.12 and imports `OCP`.

- [ ] **Step 3: Run the detection tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_detection.py -v
```

Expected: FAIL because `brep_monitor_core` does not exist.

- [ ] **Step 4: Implement the immutable core models and detection functions**

Use string enum values `PASS`, `WARNING`, `FAIL`, and `ERROR`. Store all
geometry measurements in mm, mm², and mm³. Implement compactness as
`4 * math.pi * area_mm2 / perimeter_mm**2`, returning `0.0` for non-positive
inputs. Use `epsilon=1e-15` for the severity denominators and sort category
names as `small_face`, `short_edge`, `sliver`.

- [ ] **Step 5: Run the detection tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_detection.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit Task 1**

```powershell
git add fusion_step_repair_monitor
git commit -m "feat: add repair monitor core metrics"
```

---

### Task 2: Comparison classification and versioned JSON contract

**Files:**
- Create: `fusion_step_repair_monitor/src/brep_monitor_core/classification.py`
- Create: `fusion_step_repair_monitor/src/brep_monitor_core/schema.py`
- Create: `fusion_step_repair_monitor/tests/unit/test_classification.py`
- Create: `fusion_step_repair_monitor/tests/unit/test_schema.py`

**Interfaces:**
- Consumes: Core dataclasses from Task 1.
- Produces: `classify_comparison(baseline: ShapeMetrics, candidate:
  ShapeMetrics, thresholds: ComparisonThresholds,
  symmetric_difference_percent: float | None,
  occ_warnings: Sequence[str]) -> Decision`.
- Produces: `load_request(path: Path) -> ComparisonRequest`.
- Produces: `write_result_atomic(path: Path, result: ComparisonResult) -> None`.
- Produces: `result_to_dict` and `result_from_dict` for palette transport.

- [ ] **Step 1: Write failing classification boundary tests**

Include these exact cases:

```python
def test_identical_valid_solids_pass():
    decision = classify_comparison(metrics(), metrics(), limits(), 0.0, ())
    assert decision.classification is Classification.PASS


def test_exact_fail_limit_is_warning():
    candidate = metrics(volume_mm3=1000.1)
    decision = classify_comparison(metrics(volume_mm3=1000.0), candidate,
                                   limits(), 0.0, ())
    assert decision.classification is Classification.WARNING


def test_over_fail_limit_fails():
    candidate = metrics(bbox_extents_mm=(10.011, 20.0, 30.0))
    decision = classify_comparison(metrics(), candidate, limits(), 0.0, ())
    assert decision.classification is Classification.FAIL


def test_missing_symmetric_difference_warns():
    decision = classify_comparison(metrics(), metrics(), limits(), None, ())
    assert decision.classification is Classification.WARNING
    assert "SYMMETRIC_DIFFERENCE_UNAVAILABLE" in decision.reason_codes


def test_invalid_or_multiple_solid_fails():
    decision = classify_comparison(
        metrics(), metrics(is_valid=False, solid_count=2), limits(), 0.0, ()
    )
    assert decision.classification is Classification.FAIL
```

- [ ] **Step 2: Run the classification tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_classification.py -v
```

Expected: FAIL with missing `classification` module.

- [ ] **Step 3: Implement priority-ordered classification**

Evaluate structural validity first, numeric FAIL conditions second, and
WARNING conditions third. Numeric equality with a FAIL limit remains WARNING;
only `value > limit` is FAIL. Candidate increases in small-face, short-edge,
or sliver counts are WARNING. Return every applicable reason, not only the
highest-priority reason.

- [ ] **Step 4: Write failing JSON schema and atomic-write tests**

Test schema version `1`, unsupported schema rejection, Unicode Windows paths,
round-trip preservation, and that the final result path appears only after a
successful `os.replace`.

- [ ] **Step 5: Implement JSON serialization and validation**

Reject unknown schema versions with reason code `UNSUPPORTED_SCHEMA`.
Require absolute paths. Write UTF-8 with `ensure_ascii=False`, `indent=2`, a
trailing newline, `flush()`, `os.fsync()`, and `os.replace()`.

- [ ] **Step 6: Run both unit test modules**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_classification.py tests\unit\test_schema.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit Task 2**

```powershell
git add fusion_step_repair_monitor
git commit -m "feat: classify STEP comparison results"
```

---

### Task 3: OpenCascade STEP analysis and symmetric difference

**Files:**
- Create: `fusion_step_repair_monitor/src/step_compare/__init__.py`
- Create: `fusion_step_repair_monitor/src/step_compare/occ_analysis.py`
- Create: `fusion_step_repair_monitor/tests/integration/occ_fixtures.py`
- Create: `fusion_step_repair_monitor/tests/integration/test_occ_analysis.py`

**Interfaces:**
- Consumes: `DetectionThresholds` and `ShapeMetrics`.
- Produces: `analyze_step(path: Path, thresholds: DetectionThresholds) ->
  ShapeMetrics`.
- Produces: `symmetric_difference_percent(baseline_path: Path,
  candidate_path: Path) -> SymmetricDifferenceResult`.
- Raises: `StepTransferError` for unreadable or zero-root STEP transfer.

- [ ] **Step 1: Add generated OpenCascade fixture helpers**

Implement `write_box`, `write_notched_box`, `write_open_shell`,
`write_two_solids`, and `write_truncated_step` using
`BRepPrimAPI_MakeBox`, `BRepAlgoAPI_Cut`, `TopoDS_Compound`, and
`STEPControl_Writer`. Centralize normal shape output in this exact helper:

```python
def write_shape(path: Path, shape) -> Path:
    writer = STEPControl_Writer()
    transfer_status = writer.Transfer(shape, STEPControl_AsIs)
    if transfer_status != IFSelect_RetDone:
        raise RuntimeError(f"STEP transfer failed: {transfer_status}")
    write_status = writer.Write(str(path))
    if write_status != IFSelect_RetDone:
        raise RuntimeError(f"STEP write failed: {write_status}")
    return path
```

`write_truncated_step` writes the fixed UTF-8 bytes
`b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n"` and returns its path.

OCP geometry coordinates are interpreted as mm for the external comparator.

- [ ] **Step 2: Write failing STEP metric tests**

Assert a 10 × 20 × 30 mm box has one valid closed solid, volume 6000 mm³,
surface area 2200 mm², and extents `(10, 20, 30)` within `1e-7`. Assert an
open shell is not a closed solid, a compound of two boxes reports two solids,
and truncated STEP raises `StepTransferError`.

- [ ] **Step 3: Run the integration tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_occ_analysis.py -v
```

Expected: FAIL because `occ_analysis.py` is missing.

- [ ] **Step 4: Implement STEP transfer and metrics**

Use `STEPControl_Reader`, `TopExp_Explorer`, `BRepCheck_Analyzer`,
`BRepGProp.VolumeProperties_s`, `BRepGProp.SurfaceProperties_s`,
`BRepBndLib.Add_s`, `BRep_Tool`, and `GCPnts_AbscissaPoint`. Count faces and
edges by topology, calculate every face area and boundary perimeter, and use
Task 1's compactness logic for threshold counts.

- [ ] **Step 5: Write failing symmetric-difference tests**

Assert identical boxes return `0.0%`. Assert a 1 mm³ corner notch in a
6000 mm³ box returns approximately `0.0166667%`. Add an injectable Boolean
operator factory so a forced Boolean failure returns
`available=False` with a warning string rather than raising out of the
comparator.

- [ ] **Step 6: Implement symmetric difference**

Compute `baseline \ candidate` and `candidate \ baseline` with
`BRepAlgoAPI_Cut`, validate `IsDone()`, sum positive solid volumes, divide by
baseline volume, and multiply by 100. Return unavailable if baseline volume is
non-positive or either Boolean fails.

- [ ] **Step 7: Run the OpenCascade integration tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_occ_analysis.py -v
```

Expected: all tests PASS.

- [ ] **Step 8: Commit Task 3**

```powershell
git add fusion_step_repair_monitor
git commit -m "feat: analyze STEP geometry with OpenCascade"
```

---

### Task 4: Comparator CLI and self-contained distribution

**Files:**
- Create: `fusion_step_repair_monitor/src/step_compare/cli.py`
- Create: `fusion_step_repair_monitor/src/step_compare/__main__.py`
- Create: `fusion_step_repair_monitor/tests/integration/test_cli.py`
- Create: `fusion_step_repair_monitor/step_compare.spec`
- Create: `fusion_step_repair_monitor/scripts/build_comparator.ps1`
- Create: `fusion_step_repair_monitor/scripts/verify_distribution.ps1`

**Interfaces:**
- Consumes: Task 2 schema/classification and Task 3 geometry analysis.
- Produces command: `step_compare.exe --request <absolute-request.json>`.
- Exit codes: `0` for a complete PASS/WARNING/FAIL result and `2` for ERROR.
- Produces an atomic `result_json` matching schema version `1`.

- [ ] **Step 1: Write failing end-to-end CLI tests**

Invoke `python -m step_compare --request request.json` in a subprocess. Test:

- Identical generated boxes produce PASS and exit `0`.
- An over-limit box change produces FAIL and exit `0`.
- Truncated input produces ERROR result and exit `2`.
- Unsupported request schema produces ERROR result and exit `2`.
- Paths containing Japanese characters work.

- [ ] **Step 2: Run the CLI tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_cli.py -v
```

Expected: FAIL because `step_compare.__main__` is missing.

- [ ] **Step 3: Implement CLI orchestration**

Use `argparse` with only `--request`. Always attempt to write a schema-valid
ERROR result when the request supplies a usable result path. Write diagnostic
details to stderr and keep normal stdout to one JSON status line.

- [ ] **Step 4: Run CLI and complete test suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit tests\integration -v
```

Expected: all tests PASS.

- [ ] **Step 5: Create the PyInstaller one-directory build**

In `step_compare.spec`, use `collect_all("OCP")`, include
`brep_monitor_core`, set console mode true, and name the executable
`step_compare`. `build_comparator.ps1` removes only
`build\step_compare` and `dist\step_compare` after verifying both resolved
paths are inside the project, then runs PyInstaller.

- [ ] **Step 6: Verify the distribution without Python on PATH**

Run the generated executable with an explicit request fixture from a clean
PowerShell process whose PATH contains only Windows system directories.
Assert result JSON is PASS and inspect the process list to confirm no
`python.exe` child remains.

- [ ] **Step 7: Commit Task 4**

```powershell
git add fusion_step_repair_monitor
git commit -m "feat: package standalone STEP comparator"
```

---

### Task 5: Fusion add-in shell and persistent palette

**Files:**
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/FusionStepRepairMonitor.py`
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/FusionStepRepairMonitor.manifest`
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/config.py`
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/monitor/__init__.py`
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/monitor/app.py`
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/monitor/palette_controller.py`
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/palette/index.html`
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/palette/styles.css`
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/palette/app.js`
- Create: `fusion_step_repair_monitor/tests/unit/test_addin_lifecycle.py`
- Create: `fusion_step_repair_monitor/tests/fusion_stubs/adsk/core.py`
- Create: `fusion_step_repair_monitor/tests/fusion_stubs/adsk/fusion.py`

**Interfaces:**
- Produces Fusion entry points `run(context)` and `stop(context)`.
- Produces `MonitorApplication.start()` and `MonitorApplication.stop()`.
- Produces palette actions `select_target`, `start_monitoring`,
  `compare_now`, `accept_baseline`, `stop_monitoring`,
  `open_session_folder`, and `select_candidate`.
- Produces palette update action `render_state`.

- [ ] **Step 1: Write an import/lifecycle test with Fusion stubs**

The test imports the entry module, calls `run({})`, asserts exactly one
command definition, control, palette, and handler set were created, calls
`stop({})`, and asserts all were removed. Running `stop` twice must be safe.

- [ ] **Step 2: Run the lifecycle test and verify failure**

Run:

```powershell
$env:PYTHONPATH = "tests\fusion_stubs;addin\FusionStepRepairMonitor"
.\.venv\Scripts\python.exe -m pytest tests\unit\test_addin_lifecycle.py -v
```

Expected: FAIL because the add-in files do not exist.

- [ ] **Step 3: Implement the manifest and add-in entry points**

Use a fixed UUID, `autodeskProduct: Fusion360`, `type: addin`,
`runOnStartup: false`, `supportedOS: windows`, and version `0.1.0`.
Add one command to the Design workspace Solid tab under the Add-Ins panel.
Keep strong references to every Fusion event handler.

- [ ] **Step 4: Implement a persistent local palette**

Create it with Fusion's new browser enabled. JavaScript calls
`adsk.fusionSendData(action, JSON.stringify(payload))`. Python handles
`incomingFromHTML`; Python sends full state snapshots through
`palette.sendInfoToHTML("render_state", json)`. Every JavaScript handler
returns `"OK"`.

- [ ] **Step 5: Implement stop cleanup**

Closing the palette calls `stop_monitoring`. Add-in shutdown removes
`commandTerminated`, palette, command, custom-event, and HTML handlers and
sets the worker stop event.

- [ ] **Step 6: Run the lifecycle test**

Expected: PASS and no leaked handler references in the stub registry.

- [ ] **Step 7: Commit Task 5**

```powershell
git add fusion_step_repair_monitor
git commit -m "feat: add Fusion repair monitor palette"
```

---

### Task 6: Fusion target validation, candidate extraction, and highlighting

**Files:**
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/monitor/fusion_gateway.py`
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/monitor/candidate_service.py`
- Create: `fusion_step_repair_monitor/tests/unit/test_candidate_service.py`
- Modify: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/monitor/palette_controller.py`
- Expand: `fusion_step_repair_monitor/tests/fusion_stubs/adsk/core.py`
- Expand: `fusion_step_repair_monitor/tests/fusion_stubs/adsk/fusion.py`

**Interfaces:**
- Produces `FusionGateway.select_target() -> TargetRef`.
- Produces `FusionGateway.resolve_target(TargetRef) -> TargetSnapshot`.
- Produces `FusionGateway.export_component(component, path: Path) -> None`.
- Produces `CandidateService.scan(body, thresholds) -> list[CandidateMetrics]`.
- Produces `CandidateService.highlight(face_token: str) -> None`.

- [ ] **Step 1: Write target-validation and unit-conversion tests**

Test rejection of zero bodies, two solids, a surface body, and a mesh-only
component. For a valid body, test cm-to-mm edge conversion (`0.001 cm` becomes
`0.01 mm`) and cm²-to-mm² face conversion (`0.002 cm²` becomes `0.2 mm²`).

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_candidate_service.py -v
```

Expected: FAIL with missing candidate service.

- [ ] **Step 3: Implement selection as a dedicated Fusion command**

Do not call `ui.selectEntity` from another command event. Create a temporary
selection command with a `SelectionCommandInput` filtered to `SolidBodies`.
Resolve the owning component from the selected body, then validate exactly
one solid B-Rep body in that component.

- [ ] **Step 4: Implement candidate extraction**

For each face, sum boundary-edge lengths once per edge, get face area and
entity token, call the shared core scoring function, and sort by descending
severity then ascending face area.

- [ ] **Step 5: Implement highlighting**

Resolve the face token with `design.findEntityByToken`, clear the active
selection, add the first matching face, and call `viewport.fit()` followed by
`viewport.refresh()`. If the token is stale, return a structured
`STALE_FACE_TOKEN` error and refresh the candidate list.

- [ ] **Step 6: Run candidate tests**

Expected: all tests PASS.

- [ ] **Step 7: Commit Task 6**

```powershell
git add fusion_step_repair_monitor
git commit -m "feat: detect and highlight Fusion sliver faces"
```

---

### Task 7: Session storage, STEP export, revision monitoring, and worker process

**Files:**
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/monitor/session_store.py`
- Create: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/monitor/comparison_runner.py`
- Create: `fusion_step_repair_monitor/tests/unit/test_session_store.py`
- Create: `fusion_step_repair_monitor/tests/unit/test_comparison_runner.py`
- Modify: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/monitor/app.py`
- Modify: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/monitor/fusion_gateway.py`

**Interfaces:**
- Produces `SessionStore.create(document_name, target, thresholds) ->
  SessionState`.
- Produces `SessionStore.next_candidate() -> CandidateJob`.
- Produces `SessionStore.promote(job_id: int) -> SessionState`.
- Produces `ComparisonRunner.submit(job: CandidateJob) -> None`.
- Produces `ComparisonRunner.stop(timeout_seconds: float) -> None`.
- Emits custom-event payloads `job_complete`, `job_error`, and
  `runner_stopped`.

- [ ] **Step 1: Write failing session-store tests**

Assert sanitized session names, four-digit sequence numbering, atomic
`session.json`, immutable old files, direct PASS promotion, confirmed WARNING
promotion, and rejected FAIL/ERROR promotion.

- [ ] **Step 2: Implement session storage**

Use `%LOCALAPPDATA%` from `os.environ`, reject missing values with a visible
configuration error, and sanitize document names to ASCII letters, digits,
spaces, `_`, and `-`. Resolve and verify every session path remains under the
session root before writing.

- [ ] **Step 3: Write failing runner concurrency tests**

Use a fake executable that waits, writes a result, and exits. Assert:

- Only one process runs.
- Revisions arriving while busy collapse to the newest pending revision.
- Stale job results are retained but not rendered.
- Timeout terminates the child and emits ERROR.
- `stop` terminates only the process launched by this add-in.

- [ ] **Step 4: Implement asynchronous comparison runner**

The worker thread may use `subprocess`, files, JSON, and timers but no
`adsk` calls. It fires a registered Fusion custom event containing only JSON.
The main-thread event handler reads the result and updates the palette.

- [ ] **Step 5: Implement monitoring state transitions**

Use explicit states:

```text
IDLE -> EXPORTING_BASELINE -> MONITORING
MONITORING -> EXPORTING_CANDIDATE -> COMPARING -> REVIEW -> MONITORING
any state -> STOPPING -> IDLE
```

On `commandTerminated`, resolve the target, compare `revisionId`, debounce the
same ID, export on the main thread, and then submit the external job. Fall
back from a stale body token to the component token and require one solid.

- [ ] **Step 6: Run session and runner tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_session_store.py tests\unit\test_comparison_runner.py -v
```

Expected: all tests PASS and no child process remains.

- [ ] **Step 7: Commit Task 7**

```powershell
git add fusion_step_repair_monitor
git commit -m "feat: monitor Fusion revisions and compare STEP"
```

---

### Task 8: Complete palette interactions and result presentation

**Files:**
- Modify: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/palette/index.html`
- Modify: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/palette/styles.css`
- Modify: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/palette/app.js`
- Modify: `fusion_step_repair_monitor/addin/FusionStepRepairMonitor/monitor/palette_controller.py`
- Create: `fusion_step_repair_monitor/tests/unit/test_palette_contract.py`

**Interfaces:**
- Consumes the versioned state payload produced by `MonitorApplication`.
- Emits only the seven palette actions listed in Task 5.
- Produces deterministic DOM status for UI smoke assertions.

- [ ] **Step 1: Write failing palette-contract tests**

Parse `app.js` and test the pure `renderModel(state)` function with Node:

- PASS/WARNING/FAIL/ERROR classes map to green/yellow/red/gray.
- Every non-PASS reason is rendered.
- Baseline, candidate, delta, limit, and units appear for each rule.
- Accept is enabled for PASS, confirmation-gated for WARNING, and disabled for
  FAIL/ERROR.
- Buttons are disabled during export/comparison.
- Candidate row sends its face token without injecting it into HTML.

- [ ] **Step 2: Run the palette tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_palette_contract.py -v
```

Expected: FAIL because the final render contract is absent.

- [ ] **Step 3: Implement accessible palette markup and styles**

Use native buttons and table elements, visible keyboard focus, `aria-live`
for status changes, Japanese labels, and textContent rather than innerHTML for
all model-derived strings.

- [ ] **Step 4: Implement action and render contracts**

`render_state` replaces the complete client view model. UI actions send
minimal JSON payloads and await the Promise returned by
`adsk.fusionSendData`. Display transport errors without changing monitoring
state.

- [ ] **Step 5: Run all non-OCC unit tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit Task 8**

```powershell
git add fusion_step_repair_monitor
git commit -m "feat: present repair comparison results"
```

---

### Task 9: Stage, install, and verify the complete add-in

**Files:**
- Create: `fusion_step_repair_monitor/scripts/stage_addin.ps1`
- Create: `fusion_step_repair_monitor/scripts/install_addin.ps1`
- Modify: `fusion_step_repair_monitor/scripts/verify_distribution.ps1`
- Create: `fusion_step_repair_monitor/README.md`

**Interfaces:**
- Produces: `dist/FusionStepRepairMonitor/`.
- Installs to:
  `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\FusionStepRepairMonitor`.
- Produces a verification report under `dist/verification.json`.

- [ ] **Step 1: Write distribution assertions**

`verify_distribution.ps1` must fail unless:

- Manifest and entry module names match.
- Shared core files equal canonical source hashes.
- `step_compare.exe` exists.
- No `.venv`, `__pycache__`, test, build, or source-map files are staged.
- Comparator identical-box smoke request returns PASS.
- Every installed file is inside the explicit add-in target directory.

- [ ] **Step 2: Implement staging**

Copy add-in sources, canonical shared core, and
`dist\step_compare` into a newly created staging directory. Build the staging
directory from scratch only after resolving and checking that the target is
inside the project's `dist` directory.

- [ ] **Step 3: Implement recoverable installation**

If an installed version exists, move it to a timestamped sibling backup before
copying the staged directory. Never recursively delete the AddIns root. Print
the installed path and backup path.

- [ ] **Step 4: Document build, install, use, and recovery**

Document exact PowerShell commands, Scripts and Add-Ins loading steps, target
selection, monitor loop, PASS/WARNING promotion behavior, session location,
and how to restore the timestamped backup.

- [ ] **Step 5: Build and verify**

Run:

```powershell
.\scripts\build_comparator.ps1
.\scripts\stage_addin.ps1
.\scripts\verify_distribution.ps1
.\scripts\install_addin.ps1
```

Expected: verification JSON says `passed: true`, and the installed directory
contains the self-contained comparator.

- [ ] **Step 6: Commit Task 9**

```powershell
git add fusion_step_repair_monitor
git commit -m "build: package and install Fusion repair monitor"
```

---

### Task 10: Bottom Frame regression and local Fusion smoke verification

**Files:**
- Create: `fusion_step_repair_monitor/tests/regression/test_bottom_frame.py`
- Create: `fusion_step_repair_monitor/tests/fusion_smoke_checklist.md`
- Modify: `fusion_step_repair_monitor/README.md`

**Interfaces:**
- Consumes the user's original and repaired-candidate STEP files read-only.
- Produces: `dist/bottom-frame-regression.json`.
- Produces: a completed Fusion smoke checklist with observed paths and result
  classification.

- [ ] **Step 1: Add the read-only Bottom Frame regression**

Default paths:

```text
C:\Users\backo\Downloads\02_Bottom Frame_v1.0,0724_A.step
C:\Users\backo\OneDrive\Documents\FEBio\02_Bottom Frame_v1.0,0724_A_repaired_candidate.step
```

Allow environment-variable overrides. Skip only when a file is absent.
Assert both files transfer, each contains exactly one valid solid, and the
classification/result metrics equal the checked-in expected JSON produced on
the first reviewed run.

- [ ] **Step 2: Run the complete automated test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit tests\integration tests\regression -v
```

Expected: all available tests PASS with no unexpected skips.

- [ ] **Step 3: Run the installed comparator on Bottom Frame data**

Save the full result as `dist/bottom-frame-regression.json`. Confirm the input
STEP modification timestamps and hashes are unchanged before and after.

- [ ] **Step 4: Execute the Fusion smoke workflow**

Using the local Fusion installation:

1. Load a disposable copy of the original Bottom Frame STEP.
2. Open the STEP Repair Monitor palette.
3. Select the single-solid component and start monitoring.
4. Confirm candidate rows and face highlighting.
5. Delete one candidate face with Fusion's standard delete command.
6. Confirm automatic candidate STEP export and result display.
7. Exercise Undo and confirm the document remains usable.
8. Exercise PASS or WARNING baseline promotion as available.
9. Stop the monitor and unload the add-in.
10. Confirm no `step_compare.exe` process or registered monitor remains.

Record the session directory, command ID, classification, and any Fusion log
messages in `tests/fusion_smoke_checklist.md`.

- [ ] **Step 5: Inspect Fusion and comparator logs**

Search for Python tracebacks, access violations, unhandled event errors,
missing DLL messages, stale result rendering, and leaked process IDs. Resolve
every finding and rerun the affected test.

- [ ] **Step 6: Run final distribution verification**

Run:

```powershell
.\scripts\verify_distribution.ps1
git diff --check
git status --short
```

Expected: distribution passes, no whitespace errors, and only intentional
source, test, documentation, and build-output changes are present.

- [ ] **Step 7: Commit regression evidence and documentation**

```powershell
git add fusion_step_repair_monitor
git commit -m "test: verify Fusion STEP repair monitor"
```

- [ ] **Step 8: Completion audit**

Map every acceptance criterion in the design specification to:

- A passing automated test,
- Distribution verification output, or
- A completed local Fusion smoke-check item.

Do not claim completion if any criterion has only indirect evidence.
