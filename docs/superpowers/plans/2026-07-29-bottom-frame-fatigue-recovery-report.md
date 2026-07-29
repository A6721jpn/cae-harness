# Bottom Frame Fatigue and Recovery Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce an evidence-based Japanese HTML report that screens the analyzed ABS spring for 90 displacement cycles and evaluates short-term recovery plus the unresolved risk after a 23 °C, three-year fixed-displacement hold.

**Architecture:** A small standard-library Python tool will create an instrumented load–unload derivative of the completed FEBio input and summarize its text outputs. FEBio 4.12 will rerun the original load step followed by a prescribed screw retraction step. A traceable evidence ledger and the measured solver outputs will feed a self-contained HTML report, which will be structurally and visually validated.

**Tech Stack:** Python 3.12 standard library, FEBio 4.12 headless solver, FEBio Studio 3.1 post-processing, HTML5/CSS, technical-report-authoring template and validator.

## Global Constraints

- Do not overwrite `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.feb`, its completed `.xplt`, or its `.log`.
- Use 23 °C and 26,280 hours as the agreed three-year hold condition.
- Treat 90 cycles as a displacement-controlled low-cycle screening case, not a validated fatigue-damage simulation.
- Treat the existing reactive-plasticity card as time independent; never label the release run as a three-year creep prediction.
- Use the provisional screening limits: local peak strain 1.0%, peak von Mises stress 22.5 MPa, no visible damage or functional loss after 90 cycles, and 24-hour residual displacement no more than 20% of minimum engagement margin.
- If minimum engagement margin or grade-specific long-term data is unavailable, report the affected functional decision as unresolved.
- Keep Published, Assumption, Calculation, and Inference evidence visibly distinct.
- Use only primary or authoritative technical sources for material, fatigue, creep, and software claims.
- Write the final canonical report to `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame_23C_3year_fatigue_recovery_report.html`.

---

### Task 1: Load–Unload Model Generator and Result Summarizer

**Files:**
- Create: `fatigue_recovery_analysis.py`
- Create: `tests/test_fatigue_recovery_analysis.py`

**Interfaces:**
- Consumes: a FEBio 4.0 XML file containing one load step, rigid-displacement BCs controlled by load controller 1, `Part2` Tet10 elements, and `NormalDisplacement2`.
- Produces: `prepare_release_model(source: Path, destination: Path, element_file_base: str, node_file_base: str) -> dict`, `parse_febio_series(paths: list[Path]) -> list[dict]`, `summarize_records(records: list[dict], surface_node_ids: set[int]) -> dict`, and a CLI with `prepare` and `summarize` subcommands.

- [ ] **Step 1: Write failing XML transformation tests**

Create a minimal two-element FEB fixture in `tests/test_fatigue_recovery_analysis.py`. Assert that `prepare_release_model`:

```python
self.assertEqual(root.find("./Step/step[@id='2']").get("name"), "Release")
self.assertEqual(
    [pt.text for pt in root.findall("./LoadData/load_controller[@id='1']/points/pt")],
    ["0,0", "1,1", "2,0"],
)
self.assertEqual(len(root.findall("./Step/step")), 2)
self.assertEqual(
    root.find("./Output/logfile/element_data").get("data"),
    "sx;sy;sz;sxy;syz;sxz;E1;E2;E3",
)
```

Also assert that all three rigid translations remain controlled in the release step, the rotational lock remains present, element output is restricted to `Part2`, and nodal output lists the unique nodes of `NormalDisplacement2`.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```powershell
& 'C:\Users\backo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest tests.test_fatigue_recovery_analysis -v
```

Expected: import failure because `fatigue_recovery_analysis.py` does not exist.

- [ ] **Step 3: Implement the minimal model transformation**

In `fatigue_recovery_analysis.py`, use `xml.etree.ElementTree` to:

1. validate `febio_spec version="4.0"`;
2. copy Step 1 to Step 2 and set `id="2"`, `name="Release"`;
3. retain the existing rigid rotation lock and three absolute rigid translations in Step 2;
4. replace load-controller 1 points with `0,0`, `1,1`, and `2,0`, with linear interpolation and constant extension;
5. set each step to ten increments with a maximum step size of 0.1;
6. derive `Part2` element IDs and `NormalDisplacement2` node IDs directly from XML;
7. add one `<logfile>` with:

```xml
<element_data
  data="sx;sy;sz;sxy;syz;sxz;E1;E2;E3"
  name="part2 stress strain"
  file="release_element_data.txt">1:109712:1</element_data>
<node_data
  data="ux;uy;uz"
  name="loaded surface displacement"
  file="release_surface_nodes.txt">...</node_data>
```

The actual ID list must be generated from the input instead of hard-coded.

- [ ] **Step 4: Write failing parser and calculation tests**

Use synthetic FEBio data records to assert:

```python
vm = ((0.5 * ((sx - sy)**2 + (sy - sz)**2 + (sz - sx)**2)
       + 3.0 * (sxy**2 + syz**2 + sxz**2)) ** 0.5)
self.assertAlmostEqual(summary["loaded"]["max_von_mises_mpa"], expected_vm)
self.assertAlmostEqual(summary["loaded"]["max_abs_principal_strain"], 0.009)
self.assertAlmostEqual(summary["released"]["max_surface_residual_mm"], 0.12)
self.assertAlmostEqual(summary["screening"]["stress_safety_factor"], 22.5 / expected_vm)
```

Verify that the summarizer records the controlling element or node ID, time, and source file for every maximum.

- [ ] **Step 5: Implement parser, calculations, and CLI**

Implement:

- parsing of `*Step`, `*Time`, and numeric rows from per-step FEBio text files;
- von Mises stress from the six Cauchy components;
- maximum absolute principal Green–Lagrange strain from `E1`, `E2`, `E3`;
- surface displacement magnitude from `ux`, `uy`, `uz`;
- loaded state selection at time 1 and released state selection at time 2;
- provisional stress safety factor `22.5 MPa / max von Mises stress`;
- fatigue stress amplitude and mean stress for complete release, both `max von Mises / 2`;
- JSON serialization with units, file hashes, thresholds, and explicit `time_dependent_model: false`.

- [ ] **Step 6: Run the focused and full tests**

Run:

```powershell
& 'C:\Users\backo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest tests.test_fatigue_recovery_analysis -v
& 'C:\Users\backo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all tests pass with zero errors and zero failures.

- [ ] **Step 7: Commit the tested tool**

```powershell
git add -- fatigue_recovery_analysis.py tests/test_fatigue_recovery_analysis.py
git commit -m "feat: add FEBio fatigue recovery analysis tool"
```

---

### Task 2: Derivative Release Analysis and Numeric Evidence

**Files:**
- Create: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_release.feb`
- Create: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_release.log`
- Create: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_release.xplt`
- Create: `reports/bottom-frame-fatigue-recovery/analysis_summary.json`

**Interfaces:**
- Consumes: `fatigue_recovery_analysis.py prepare`, the completed original FEB, and FEBio 4.12.
- Produces: a normal-termination load–unload solve and a JSON summary used by the report.

- [ ] **Step 1: Record source hashes and completion evidence**

Hash the original FEB, XPLT, and log with SHA-256. Confirm the source log contains `N O R M A L   T E R M I N A T I O N`, 161,419 nodes, and 109,713 solid elements.

- [ ] **Step 2: Generate the derivative input**

Run:

```powershell
& 'C:\Users\backo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\fatigue_recovery_analysis.py prepare `
  --source 'C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.feb' `
  --output 'C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_release.feb'
```

Expected: source hash printed, destination created, two steps present, load-controller values 0 → 1 → 0, and source hash unchanged.

- [ ] **Step 3: Run a bounded initialization smoke test**

Copy the derivative FEB into a timestamped debug directory, run:

```powershell
& 'C:\Program Files\FEBioStudio\bin\febio4.exe' -i '<debug-copy>.feb'
```

Stop only the exact debug PID after convergence beyond time 0.1. Confirm input success and absence of negative Jacobian, invalid load controller, rigid-body singularity, and model initialization failure.

- [ ] **Step 4: Run the full derivative analysis**

Run the canonical derivative FEB headlessly and wait for completion while reporting progress at least once per minute. The required terminal evidence is:

- convergence at time 1.0 and time 2.0;
- `N O R M A L   T E R M I N A T I O N`;
- no negative Jacobian or model-initialization error;
- non-empty XPLT and text data files for the loaded and released states.

- [ ] **Step 5: Summarize final loaded and released states**

Run:

```powershell
& 'C:\Users\backo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .\fatigue_recovery_analysis.py summarize `
  --input-directory 'C:\dev\FEBio\jobs\jobs' `
  --model 'C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_release.feb' `
  --output '.\reports\bottom-frame-fatigue-recovery\analysis_summary.json'
```

Expected JSON fields:

```json
{
  "loaded": {
    "time": 1.0,
    "max_von_mises_mpa": 0.0,
    "controlling_element_id": 0,
    "max_abs_principal_strain": 0.0
  },
  "released": {
    "time": 2.0,
    "max_surface_residual_mm": 0.0,
    "controlling_node_id": 0
  },
  "screening": {
    "stress_limit_mpa": 22.5,
    "strain_limit": 0.01,
    "stress_safety_factor": 0.0
  },
  "limitations": {
    "time_dependent_model": false
  }
}
```

The zeros above are schema examples, not expected physical values.

- [ ] **Step 6: Check peak locality**

Map the controlling element centroid against:

- the fixed surfaces;
- the M2 contact surface;
- the previously identified low-quality mesh tail if a compatible quality map is available.

Record whether the peak is interior, at contact, at a constraint, or at an isolated low-quality element. Do not discard a peak; classify its reliability.

- [ ] **Step 7: Commit the machine-readable summary**

```powershell
git add -- reports/bottom-frame-fatigue-recovery/analysis_summary.json
git commit -m "data: record Bottom Frame release analysis"
```

---

### Task 3: Authoritative Research and Evidence Ledger

**Files:**
- Create: `reports/bottom-frame-fatigue-recovery/evidence-ledger.md`

**Interfaces:**
- Consumes: the reference report, the current INEOS Terluran GP-22 technical data, ISO 899-1, ASTM D2990, ASTM D7791, the Covestro snap-fit guide, and primary fatigue research.
- Produces: a ledger with Published, Assumption, Calculation, and Inference rows that the HTML report cites directly.

- [ ] **Step 1: Verify primary sources**

Confirm current direct pages or PDFs for:

- INEOS Styrolution Terluran GP-22 short-term properties;
- ISO 899-1 plastics creep test applicability;
- ASTM D2990 creep and creep-rupture methods;
- ASTM D7791 uniaxial fatigue of plastics;
- Covestro snap-fit design guidance;
- Mura, Ricci, and Canavese fatigue testing of injection-molded ABS near room temperature.

Record access date, material grade, specimen type, temperature, load control, duration or cycles, and limitations. Do not import conclusions from the reference HTML without opening the cited primary source.

- [ ] **Step 2: Search for grade-specific 23 °C long-term data**

Search INEOS and primary literature for Terluran GP-22 creep modulus, isochronous stress–strain, stress relaxation, and recovery near 23 °C out to 26,280 hours.

Classify the result:

- exact grade and condition;
- ABS-family proxy;
- no applicable data.

Do not extrapolate three-year residual displacement from a short-term modulus unless a time-temperature model and recovery law are supported.

- [ ] **Step 3: Build the evidence ledger**

Use exactly:

```markdown
| ID | Type | Claim or value | Applicability | Source | Report section |
|---|---|---|---|---|---|
```

Include the source analysis hash, solver maxima, provisional 22.5 MPa and 1.0% criteria, 26,280-hour condition, and the distinction between rate-independent release and long-term recovery.

- [ ] **Step 4: Check equations and sensitivity**

Verify units and substitutions for:

```text
sigma_vm = sqrt(0.5[(sx-sy)^2+(sy-sz)^2+(sz-sx)^2] + 3[sxy^2+syz^2+sxz^2])
n_screen = 22.5 MPa / sigma_vm,max
sigma_a = sigma_m = sigma_vm,max / 2  for 0 -> peak -> 0 screening
residual_ratio = released_surface_residual / 2.5 mm
```

Show sensitivity to using 45 MPa yield instead of the 22.5 MPa screening allowable and to an unknown engagement margin.

- [ ] **Step 5: Commit the ledger**

```powershell
git add -- reports/bottom-frame-fatigue-recovery/evidence-ledger.md
git commit -m "docs: add fatigue recovery evidence ledger"
```

---

### Task 4: Report Authoring and Contour Evidence

**Files:**
- Create: `reports/bottom-frame-fatigue-recovery/02_Bottom_Frame_23C_3year_fatigue_recovery_report.html`
- Create: `reports/bottom-frame-fatigue-recovery/loaded_von_mises.png`
- Create: `reports/bottom-frame-fatigue-recovery/released_displacement.png`
- Create: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame_23C_3year_fatigue_recovery_report.html`

**Interfaces:**
- Consumes: `analysis_summary.json`, `evidence-ledger.md`, the derivative XPLT, and the technical report template.
- Produces: a decision-ready standalone report plus traceable contour images.

- [ ] **Step 1: Capture loaded and released contour views**

Open the derivative XPLT in FEBio Studio. At time 1.0, display effective/von Mises stress and capture a view that shows the controlling region and legend. At time 2.0, display displacement magnitude or residual displacement and capture the same camera orientation.

Cross-check the displayed extrema against `analysis_summary.json`. If Studio nodal averaging differs from element-average log output, label both values and explain the averaging difference.

- [ ] **Step 2: Draft all required semantic sections**

Adapt `C:\Users\backo\.codex\skills\technical-report-authoring\assets\technical-report-template.html` and preserve these `data-report-section` roles:

1. executive-summary;
2. context;
3. scope;
4. method;
5. data;
6. calculation;
7. results;
8. recommendations;
9. verification;
10. limitations;
11. references.

The executive summary must separately answer:

- 90-cycle fatigue: preliminary pass, preliminary fail, or unresolved;
- short-term release: calculated residual and provisional interpretation;
- 23 °C, three-year return: supported, not supported, or unresolved.

- [ ] **Step 3: State the dominant decision limits**

The report must plainly state:

- rate-independent FEA cannot predict three-year stress relaxation or recovery;
- absence of a fatigue damage law prevents a guaranteed 90-cycle life;
- a peak limited to contact, constraint, or poor mesh needs local refinement before design release;
- without minimum engagement margin, dimensional recovery cannot receive a functional pass;
- without grade-specific creep/recovery data, the three-year conclusion remains unresolved even if immediate unloading returns close to zero.

- [ ] **Step 4: Add a verification matrix**

Specify a minimum physical program:

- production-equivalent material and molding;
- 23 °C fixed-displacement hold at 1, 10, 100, 1,000, 8,760, and 26,280 hours;
- release measurements immediately, 1 hour, and 24 hours after each interval;
- 90 cycles after the long hold, plus 270 development-margin cycles;
- natural position, engagement margin, insertion/removal force, whitening, cracks, and lock function;
- at least three lots with sample count set by product risk and quality plan.

- [ ] **Step 5: Mirror the canonical report**

Copy the validated repository report byte-for-byte to:

```text
C:\dev\FEBio\jobs\jobs\02_Bottom_Frame_23C_3year_fatigue_recovery_report.html
```

Record SHA-256 hashes for both paths and require equality.

- [ ] **Step 6: Commit report sources and images**

```powershell
git add -- reports/bottom-frame-fatigue-recovery
git commit -m "docs: report Bottom Frame fatigue and recovery"
```

---

### Task 5: Structural, Visual, and Final Engineering Verification

**Files:**
- Verify: `reports/bottom-frame-fatigue-recovery/02_Bottom_Frame_23C_3year_fatigue_recovery_report.html`
- Verify: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame_23C_3year_fatigue_recovery_report.html`

**Interfaces:**
- Consumes: completed report and all evidence artifacts.
- Produces: a verified deliverable with explicit remaining uncertainties.

- [ ] **Step 1: Run structural validation**

Run:

```powershell
& 'C:\Users\backo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  'C:\Users\backo\.codex\skills\technical-report-authoring\scripts\validate_report.py' `
  '.\reports\bottom-frame-fatigue-recovery\02_Bottom_Frame_23C_3year_fatigue_recovery_report.html'
```

Expected: `OK`.

- [ ] **Step 2: Run report and analysis tests**

Run:

```powershell
& 'C:\Users\backo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: zero errors and zero failures.

- [ ] **Step 3: Inspect desktop and narrow layouts**

Open the local report in a permitted browser. Inspect at approximately 1440 px and 390 px widths:

- no table or URL overflow;
- readable equations and Japanese text;
- visible evidence labels without reliance on color;
- legible contour figures and captions;
- continuous heading hierarchy and visible focus state.

- [ ] **Step 4: Inspect print presentation**

Open print preview and verify:

- sources remain readable;
- tables do not clip;
- headings are not stranded at page bottoms;
- contour figures fit within page margins;
- navigation-only elements are suppressed.

- [ ] **Step 5: Perform the final evidence gate**

Re-run and read:

- original and derivative SHA-256 hashes;
- derivative normal-termination check;
- JSON schema and numeric maxima;
- report validator;
- repository test suite;
- canonical/repository report hash equality.

Report the actual fatigue screening result, immediate recovery result, and three-year uncertainty without upgrading an inference into a validated product claim.
