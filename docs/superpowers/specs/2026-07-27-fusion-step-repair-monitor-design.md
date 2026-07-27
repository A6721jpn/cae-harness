# Fusion STEP Repair Monitor — Design

## 1. Purpose

Build a safety-oriented Autodesk Fusion add-in that helps a user repair
micro-faces, short edges, and sliver faces in an imported STEP solid.

The add-in detects and highlights suspicious B-Rep topology, but it does not
implement geometry healing. The user performs the repair with Fusion's
standard face-delete/heal operation. The add-in observes the resulting body
revision, exports before-and-after STEP files, invokes an external
OpenCascade-based comparator, and reports PASS, WARNING, FAIL, or ERROR.

The add-in never performs automatic undo, automatic face deletion, or
automatic acceptance of a result.

## 2. Initial Scope

The first release supports:

- Autodesk Fusion on Windows.
- One selected component containing exactly one B-Rep solid body.
- Imported STEP geometry and other ordinary Fusion B-Rep solids that satisfy
  the same one-component/one-solid constraint.
- Fusion's standard face-delete/heal workflow.
- Automatic comparison after the monitored body changes.
- Manual promotion of an accepted candidate to the next comparison baseline.

The first release does not support:

- Components containing zero or multiple solid bodies.
- Mesh bodies, T-Spline bodies, or surface-only bodies.
- Assemblies in which the selected component must be compared in multiple
  occurrence transforms.
- Automatic repair, automatic undo, or automatic baseline promotion.
- Cloud services or network communication.

## 3. Architecture

The product has two isolated processes.

### 3.1 Fusion Python add-in

The add-in owns:

- The toolbar command and persistent HTML palette.
- Component selection and scope validation.
- In-Fusion candidate detection and highlighting.
- Session state and body revision tracking.
- STEP export through Fusion's `ExportManager`.
- Asynchronous external-process invocation.
- Rendering comparison results in the palette.

All Fusion API calls occur on Fusion's main thread. The external comparator
does not load Fusion libraries or modify the Fusion document.

### 3.2 External STEP comparator

The comparator is a self-contained Windows executable built from Python and
OpenCascade bindings. It does not require a system Python installation.
The build uses a pinned CPython/OpenCascade/PyInstaller toolchain and produces
a one-directory distribution so OpenCascade DLL discovery is deterministic.

It accepts a request JSON path, reads the referenced baseline and candidate
STEP files, and writes a result JSON atomically. It performs no write to the
input STEP files.

The installed add-in layout contains the comparator and its runtime together:

```text
FusionStepRepairMonitor/
  FusionStepRepairMonitor.py
  FusionStepRepairMonitor.manifest
  commands/
  lib/
  palette/
  bin/
    step_compare/
      step_compare.exe
      runtime files
```

## 4. Session State and Storage

Session data is stored under:

```text
%LOCALAPPDATA%\BRepSliverMonitor\sessions\
  YYYYMMDD-HHMMSS-<sanitized-document-name>\
```

Each session contains:

```text
baseline-0000.step
baseline-0000.metrics.json
candidate-0001.step
candidate-0001.request.json
candidate-0001.result.json
candidate-0001.stdout.log
candidate-0001.stderr.log
session.json
```

Later comparisons increment the four-digit candidate number. Accepting a
candidate records its STEP and metrics as the new logical baseline in
`session.json`; previous files remain unchanged for auditability.

The session state records:

- Fusion document identity.
- Selected component entity token.
- Selected body entity token.
- Last accepted body revision ID.
- Current observed body revision ID.
- Detection and comparison thresholds.
- Baseline and candidate file names.
- The Fusion command ID associated with each observed change when available.
- Comparison status and timestamps.

## 5. User Interface

A `STEP Repair Monitor` button is added to the Design workspace. It opens a
persistent palette with these sections.

### 5.1 Target

- Select component.
- Display the component and body names.
- Display validation status.
- Reject a target unless it contains exactly one solid B-Rep body.

### 5.2 Detection thresholds

- Automatic preset toggle, enabled by default.
- Short-edge threshold in mm.
- Small-face threshold in mm².
- Sliver compactness threshold.
- Manual overrides for all three values.

### 5.3 Candidate list

Each row shows:

- Candidate category or categories.
- Face area in mm².
- Minimum boundary-edge length in mm.
- Compactness.
- Severity score.

Selecting a row selects and highlights the corresponding face in Fusion and
fits the view to it. Candidates are sorted by decreasing severity.

### 5.4 Monitoring and results

Controls:

- Start monitoring.
- Compare now.
- Accept as new baseline.
- Stop monitoring.
- Open session folder.

The result view uses:

- Green: PASS.
- Yellow: WARNING.
- Red: FAIL.
- Gray: ERROR or inactive.

It displays the baseline value, candidate value, absolute delta, relative
delta where applicable, configured limit, and the reason for every non-PASS
classification.

## 6. Candidate Detection

Fusion API database lengths are converted from cm to mm before display or
classification. Areas are converted from cm² to mm².

Let `D` be the selected body's axis-aligned bounding-box diagonal in mm.
Automatic thresholds are:

```text
short_edge_mm = clamp(D × 1e-4, 0.001, 0.05)
small_face_mm2 = clamp(D² × 2e-5, 0.0001, 1.0)
sliver_compactness = 0.01
```

For a face with area `A` and total boundary perimeter `P`, compactness is:

```text
C = 4πA / P²
```

A face is a candidate if any of these is true:

- `A <= small_face_mm2`.
- Its minimum boundary-edge length is `<= short_edge_mm`.
- `C <= sliver_compactness`.

Degenerate or non-measurable edges receive the highest severity and remain
visible as candidates.

The severity score is the maximum of:

```text
small_face_mm2 / max(A, epsilon)
short_edge_mm / max(min_edge_length, epsilon)
sliver_compactness / max(C, epsilon)
```

Candidate face entity tokens are valid only for the body revision in which
they were collected. The list is cleared when the body revision changes and
is recomputed after a candidate is accepted as the new baseline.

## 7. Monitoring Workflow

### 7.1 Start monitoring

1. Validate one component with one solid body.
2. Record its component token, body token, and revision ID.
3. Compute and display suspicious-face candidates.
4. Export `baseline-0000.step`.
5. Run the comparator in single-file metrics mode to validate the baseline.
6. Enter the `MONITORING` state only if the baseline is readable and valid.

### 7.2 Detect a Fusion edit

The add-in subscribes to Fusion's `commandTerminated` event. After a command
terminates:

1. Ignore the event unless monitoring is active.
2. Resolve the monitored body from its entity token.
3. If the body token no longer resolves, resolve the component token and
   require that it still contains exactly one solid body.
4. Read the body's revision ID.
5. Do nothing if the revision ID is unchanged.
6. Debounce duplicate events for the same revision ID.
7. Export the selected component to the next candidate STEP.
8. Start the external comparator without blocking Fusion.

The revision check is authoritative; the workflow does not depend on a
localized command name. A body change caused by a command other than face
deletion is also compared and is identified by its command ID in the report.

### 7.3 Review

When comparison completes, the palette displays the classification and
metrics. Monitoring does not automatically change the baseline.

- `Accept as new baseline` updates the logical baseline to the current
  candidate, records the current revision ID, and refreshes face candidates.
- PASS results can be accepted directly. WARNING results require an explicit
  confirmation dialog. FAIL and ERROR results cannot be promoted.
- If the user performs Fusion Undo, the resulting revision is treated as
  another candidate unless it exactly matches the accepted baseline metrics.
- `Compare now` exports and compares the current revision even if an automatic
  event was missed.
- `Stop monitoring` cancels observation after any active comparison finishes
  or is terminated by the user.

## 8. Comparator Contract

The request JSON contains:

```json
{
  "schema_version": 1,
  "baseline_step": "absolute path",
  "candidate_step": "absolute path",
  "result_json": "absolute path",
  "thresholds": {
    "short_edge_mm": 0.01,
    "small_face_mm2": 0.2,
    "sliver_compactness": 0.01,
    "volume_delta_percent": 0.01,
    "bbox_delta_mm": 0.01,
    "symmetric_difference_percent": 0.02,
    "warning_fraction": 0.8
  }
}
```

Paths are absolute and encoded as UTF-8 JSON strings. Schema versions other
than `1` return ERROR.

The result JSON contains:

- Schema version and comparator version.
- Overall classification.
- One or more machine-readable reason codes.
- Human-readable Japanese messages.
- Baseline metrics.
- Candidate metrics.
- Delta metrics.
- Per-rule classification and limit.
- Comparator duration.
- OpenCascade errors and warnings when applicable.

The comparator writes a temporary result file in the same directory, flushes
and closes it, then renames it to the requested result path. The add-in never
reads a partially written JSON file.

## 9. Geometry Metrics

For each STEP file, the comparator reports:

- STEP transfer success.
- Number of solids, shells, faces, edges, and vertices.
- OpenCascade validity result.
- Closed-solid status.
- Volume in mm³.
- Surface area in mm².
- Axis-aligned bounding-box minimum, maximum, and extents in mm.
- Minimum face area in mm².
- Minimum edge length in mm.
- Counts below the configured small-face and short-edge thresholds.
- Count below the sliver compactness threshold.

The pairwise comparison additionally attempts a Boolean symmetric difference:

```text
volume(baseline \ candidate) + volume(candidate \ baseline)
```

The value is normalized by baseline volume and reported as a percentage.
Failure of this Boolean calculation produces WARNING rather than a fabricated
numeric result.

Surface-area delta and topology-count deltas are reported but do not directly
cause FAIL because a successful face heal is expected to change them.

## 10. Classification Rules

Default editable limits:

```text
absolute volume delta:       0.01%
bounding-box extent delta:   0.01 mm per axis
symmetric-difference volume: 0.02%
warning boundary:            80% of each FAIL limit
```

Rules are evaluated in priority order.

### 10.1 ERROR

ERROR applies if:

- STEP export did not create a readable file.
- The comparator could not start or exceeded its timeout.
- Either STEP could not be transferred by OpenCascade.
- Request or result JSON is missing, malformed, or has an unsupported schema.
- An unexpected comparator exception prevented a complete result.

### 10.2 FAIL

FAIL applies if either shape:

- Does not contain exactly one solid.
- Is not a closed solid.
- Fails OpenCascade validity checks.

FAIL also applies if any of these limits is exceeded:

- Absolute relative volume delta.
- Any one of the three bounding-box extent deltas.
- Symmetric-difference volume, when successfully calculated.

### 10.3 WARNING

WARNING applies if no ERROR or FAIL rule applies and any of these is true:

- A numeric FAIL metric is at least 80% of its limit.
- Candidate small-face count is greater than baseline.
- Candidate short-edge count is greater than baseline.
- Candidate sliver-face count is greater than baseline.
- Symmetric-difference calculation failed.
- OpenCascade produced a non-fatal transfer or Boolean warning.

### 10.4 PASS

PASS applies only when no ERROR, FAIL, or WARNING condition applies.

## 11. Concurrency and Failure Handling

Only one export/comparison job runs at a time.

- If another body revision occurs during a comparison, the add-in remembers
  only the newest pending revision and compares it after the active job ends.
- Export failure leaves the accepted baseline unchanged.
- Comparator timeout terminates only the external process and returns ERROR.
- Closing the document or deleting the selected component stops monitoring
  and records the reason.
- Closing the palette hides the UI but does not silently continue monitoring;
  it stops the monitor.
- Add-in shutdown removes all Fusion event handlers and terminates a child
  comparator process started by the add-in.
- A stale result whose candidate number does not match the active job is
  ignored and retained in the session folder.

The default external-process timeout is 120 seconds and is configurable in an
advanced settings file.

## 12. Testing

### 12.1 Pure unit tests

Run outside Fusion:

- Automatic threshold calculations and unit conversions.
- Compactness and severity calculations.
- Classification priority and all boundary values.
- Request/result JSON validation.
- Session numbering and baseline promotion.
- Atomic result-file handling.

### 12.2 OpenCascade integration tests

Use generated and checked-in fixtures:

- Identical STEP files: PASS.
- A valid micro-feature repair within limits: PASS or WARNING according to
  the configured topology-count rules.
- A shape change at exactly 80% of a limit: WARNING.
- A shape change exactly at a FAIL limit: WARNING.
- A shape change greater than a FAIL limit: FAIL.
- Open shell: FAIL.
- Multiple solids: FAIL.
- Invalid or truncated STEP: ERROR.
- A Boolean symmetric-difference failure with otherwise valid solids:
  WARNING.

### 12.3 Regression data

Compare:

- `02_Bottom Frame_v1.0,0724_A.step`.
- `02_Bottom Frame_v1.0,0724_A_repaired_candidate.step`.

The test records all geometry metrics and asserts deterministic
classification under a checked-in threshold configuration. It does not
modify either input file.

### 12.4 Fusion smoke test

On the installed local Fusion version:

1. Load a one-component/one-solid test STEP.
2. Open the palette and select the component.
3. Start monitoring and verify baseline export.
4. Select a highlighted candidate.
5. Use Fusion's standard face deletion.
6. Verify automatic revision detection and candidate export.
7. Verify result display and session artifacts.
8. Use Fusion Undo and verify that the document remains usable.
9. Accept a passing candidate and verify candidate refresh.
10. Stop the add-in and confirm event handlers and child processes are gone.

## 13. Acceptance Criteria

The feature is complete when:

- The add-in installs and loads in the user's Fusion installation.
- It rejects unsupported component/body configurations without modifying the
  document.
- It detects and highlights candidates with automatic and editable
  thresholds.
- It exports a baseline and automatically exports a changed revision after a
  standard Fusion face deletion.
- The self-contained comparator runs without system Python.
- The palette receives and displays PASS, WARNING, FAIL, and ERROR results.
- Baseline promotion is manual and preserves prior session files.
- No workflow path automatically deletes geometry or invokes Undo.
- Unit and OpenCascade integration tests pass.
- The Fusion smoke test completes on the local installation.
