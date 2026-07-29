# Bottom Frame 0729C Local-Refinement Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and solve three revised-STEP FEBio models with a 2.0 mm global mesh, 0.70/0.50/0.40 mm lower-arm local meshes, and a 2.0 mm rigid-screw push stroke.

**Architecture:** Extend the existing FEBio-Gmsh launcher with a reusable distance/threshold local-refinement field and a geometric-signature selector. A job-specific builder will reuse the completed 0729B analysis conditions, rebuild the 0729C mesh and rigid screw, then run the three cases sequentially and compare their XPLT results.

**Tech Stack:** Python 3.12 venv in the FEBio-Gmsh launcher worktree, Gmsh 4.15.2, NumPy, FEBio 4.12 CLI, FEBio Studio 3.1 FBS Python module, pytest.

## Global Constraints

- Source STEP is `C:\Users\backo\Downloads\02_Bottom Frame_v1.0,0729_C.step`.
- Required STEP SHA-256 is `2263F8913B9694A996B994772ED6FB45B1BFAF1A62DD850B77598E27FA2B57E6`.
- Global mesh target is exactly `2.0 mm`.
- Local targets are exactly `0.70 mm`, `0.50 mm`, and `0.40 mm`.
- Threshold transition distance is exactly `3.0 mm`.
- Rigid-screw push stroke is exactly `2.0 mm`, opposite the selected contact-face outward normal.
- Material, constraints, contact, solver steps, controller, and output settings remain identical across the three cases.
- Runs are sequential because the machine has about 32 GB physical RAM.
- Existing completed jobs and unrelated dirty-worktree files are never overwritten or committed.

---

### Task 1: Add reusable local-refinement field support

**Files:**
- Create: `.worktrees/febio-gmsh-launcher/febio_gmsh_launcher/src/febio_gmsh_launcher/local_refinement.py`
- Create: `.worktrees/febio-gmsh-launcher/febio_gmsh_launcher/tests/test_local_refinement.py`

**Interfaces:**
- Consumes: an initialized Gmsh model, CAD face tags, and millimetre mesh-size inputs.
- Produces: `LocalRefinementConfig`, `FaceSignature`, `face_signature()`, `configure_distance_threshold_field()`, and `validate_seed_signatures()`.

- [ ] **Step 1: Write tests for config validation and signature stability**

```python
from febio_gmsh_launcher.local_refinement import (
    FaceSignature,
    LocalRefinementConfig,
    validate_seed_signatures,
)


def test_local_refinement_config_accepts_approved_sizes():
    config = LocalRefinementConfig(
        global_size_mm=2.0,
        local_size_mm=0.40,
        transition_mm=3.0,
    )
    assert config.global_size_mm == 2.0
    assert config.local_size_mm == 0.40


def test_local_size_must_be_smaller_than_global_size():
    with pytest.raises(ValueError, match="local_size_mm"):
        LocalRefinementConfig(2.0, 2.0, 3.0)


def test_seed_signature_validation_rejects_empty_selection():
    with pytest.raises(ValueError, match="nonempty"):
        validate_seed_signatures([])
```

- [ ] **Step 2: Run the focused tests and verify they fail before implementation**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest febio_gmsh_launcher/tests/test_local_refinement.py -q
```

Working directory:
`C:\Users\backo\OneDrive\Documents\FEBio\.worktrees\febio-gmsh-launcher`

Expected: collection fails because `febio_gmsh_launcher.local_refinement` does not exist.

- [ ] **Step 3: Implement the configuration, signatures, and Gmsh field**

```python
@dataclass(frozen=True)
class LocalRefinementConfig:
    global_size_mm: float
    local_size_mm: float
    transition_mm: float

    def __post_init__(self) -> None:
        if not 0.0 < self.local_size_mm < self.global_size_mm:
            raise ValueError("local_size_mm must be positive and below global_size_mm")
        if self.transition_mm <= 0.0:
            raise ValueError("transition_mm must be positive")


def configure_distance_threshold_field(
    face_tags: Sequence[int],
    config: LocalRefinementConfig,
) -> tuple[int, int]:
    if not face_tags:
        raise ValueError("local-refinement face selection must be nonempty")
    distance = gmsh.model.mesh.field.add("Distance")
    gmsh.model.mesh.field.setNumbers(distance, "FacesList", list(face_tags))
    gmsh.model.mesh.field.setNumber(distance, "Sampling", 200)
    threshold = gmsh.model.mesh.field.add("Threshold")
    gmsh.model.mesh.field.setNumber(threshold, "InField", distance)
    gmsh.model.mesh.field.setNumber(threshold, "SizeMin", config.local_size_mm)
    gmsh.model.mesh.field.setNumber(threshold, "SizeMax", config.global_size_mm)
    gmsh.model.mesh.field.setNumber(threshold, "DistMin", 0.0)
    gmsh.model.mesh.field.setNumber(threshold, "DistMax", config.transition_mm)
    gmsh.model.mesh.field.setAsBackgroundMesh(threshold)
    return distance, threshold
```

`FaceSignature` stores the face tag, area, centroid, and six-value bounding
box. `validate_seed_signatures()` rejects an empty list, nonpositive areas,
nonfinite coordinates, and duplicate tags.

- [ ] **Step 4: Run the new test and the existing launcher suite**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest febio_gmsh_launcher/tests/test_local_refinement.py -q
& '.\.venv\Scripts\python.exe' -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit only the launcher source and test**

```powershell
git add febio_gmsh_launcher/src/febio_gmsh_launcher/local_refinement.py febio_gmsh_launcher/tests/test_local_refinement.py
git commit -m "feat: add distance-field local mesh refinement"
```

---

### Task 2: Inventory 0729C faces and freeze the lower-arm seed signatures

**Files:**
- Create: `C:\dev\FEBio\jobs\0729C_CAE_local_refinement_2p0mm\inspect_0729c_refinement.py`
- Create: `C:\dev\FEBio\jobs\0729C_CAE_local_refinement_2p0mm\refinement-region.json`
- Create: `C:\dev\FEBio\jobs\0729C_CAE_local_refinement_2p0mm\refinement-region-preview.png`

**Interfaces:**
- Consumes: the 0729C STEP and the user screenshot defining the lower curved assembly.
- Produces: a nonempty list of geometric `FaceSignature` records used unchanged by all three builds.

- [ ] **Step 1: Write the inspection script with a hash precondition**

The script must:

```python
STEP_PATH = Path(r"C:\Users\backo\Downloads\02_Bottom Frame_v1.0,0729_C.step")
EXPECTED_STEP_SHA256 = "2263F8913B9694A996B994772ED6FB45B1BFAF1A62DD850B77598E27FA2B57E6"

if sha256(STEP_PATH) != EXPECTED_STEP_SHA256:
    raise RuntimeError("0729C STEP hash changed")
```

It imports the STEP, emits front/side orthographic previews with CAD face tags,
and records every face's area, centroid, and bounding box.

- [ ] **Step 2: Run the inventory script**

Run:

```powershell
& 'C:\Users\backo\OneDrive\Documents\FEBio\.worktrees\febio-gmsh-launcher\.venv\Scripts\python.exe' `
  'C:\dev\FEBio\jobs\0729C_CAE_local_refinement_2p0mm\inspect_0729c_refinement.py'
```

Expected: one solid volume, a complete face inventory, and preview images.

- [ ] **Step 3: Select the complete lower assembly by geometric signature**

Use the front-view preview to include the lower arm's upper-left free end,
curved span, inner hook/spring, and right terminal block. Exclude the upper
housing, central mounting hole, and upper frame. Store the approved signature
records in `refinement-region.json`; the selection must be spatial and
connectivity-aware, not a bare list of transient STEP face tags.

- [ ] **Step 4: Verify the frozen selection**

Run the inspection script with `--verify
C:\dev\FEBio\jobs\0729C_CAE_local_refinement_2p0mm\refinement-region.json`.

Expected: every stored signature remaps uniquely, all selected areas are
positive, the union bounds cover the lower arm and terminal block, and no
selected centroid lies in the excluded upper-body region.

---

### Task 3: Build one parameterized 0729C FEB generator

**Files:**
- Create: `C:\dev\FEBio\jobs\0729C_CAE_local_refinement_2p0mm\build_0729c_local_case.py`
- Test: `C:\dev\FEBio\jobs\0729C_CAE_local_refinement_2p0mm\test_build_0729c_local_case.py`
- Reference: `C:\dev\FEBio\jobs\0729B_CAE_Gmsh_2p22mm_fine075\build_0729b_model_fine075.py`

**Interfaces:**
- Consumes: `--local-size {0.70,0.50,0.40}`, `--output-dir`, the frozen region JSON, the 0729C STEP, and the completed 0729B FEB.
- Produces: a new FEB, `build-report.json`, build stdout/stderr, and mesh-quality/refinement preview artifacts.

- [ ] **Step 1: Write tests for case naming and 2.0 mm displacement**

```python
def test_case_name_is_deterministic():
    assert case_token(0.70) == "local070"
    assert case_token(0.50) == "local050"
    assert case_token(0.40) == "local040"


def test_rigid_displacement_has_two_mm_norm():
    normal = np.array([0.3622520077, -0.0618251998, -0.9300274875])
    displacement = rigid_displacement(normal, 2.0)
    np.testing.assert_allclose(np.linalg.norm(displacement), 2.0, atol=1e-10)
    np.testing.assert_allclose(displacement, -2.0 * normal, atol=1e-10)
```

- [ ] **Step 2: Run the focused tests and confirm the missing functions fail**

Run:

```powershell
& 'C:\Users\backo\OneDrive\Documents\FEBio\.worktrees\febio-gmsh-launcher\.venv\Scripts\python.exe' `
  -m pytest 'C:\dev\FEBio\jobs\0729C_CAE_local_refinement_2p0mm\test_build_0729c_local_case.py' -q
```

Expected: import or attribute failure for `case_token` and
`rigid_displacement`.

- [ ] **Step 3: Implement the parameterized builder**

Copy the verified 0729B mapping, Tet10 ordering, curved-midnode relaxation,
rigid indenter, XML replacement, and build-report logic. Change only:

```python
gmsh.option.setNumber("Mesh.MeshSizeMin", local_size_mm)
gmsh.option.setNumber("Mesh.MeshSizeMax", 2.0)
gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
configure_distance_threshold_field(seed_face_tags, LocalRefinementConfig(
    global_size_mm=2.0,
    local_size_mm=local_size_mm,
    transition_mm=3.0,
))
displacement = -2.0 * normal
```

The builder refuses overwrite, verifies the source hash, remaps frozen face
signatures, asserts G8 invalid count zero, and records local/global sizes,
transition distance, selected signatures, mesh counts, quality, and screw
displacement in `build-report.json`.

- [ ] **Step 4: Run unit tests and compile the builder**

Run:

```powershell
& 'C:\Users\backo\OneDrive\Documents\FEBio\.worktrees\febio-gmsh-launcher\.venv\Scripts\python.exe' `
  -m pytest 'C:\dev\FEBio\jobs\0729C_CAE_local_refinement_2p0mm\test_build_0729c_local_case.py' -q
& 'C:\Users\backo\OneDrive\Documents\FEBio\.worktrees\febio-gmsh-launcher\.venv\Scripts\python.exe' `
  -m py_compile 'C:\dev\FEBio\jobs\0729C_CAE_local_refinement_2p0mm\build_0729c_local_case.py'
```

Expected: all tests pass and compilation exits zero.

---

### Task 4: Generate and preflight the three FEB models

**Files:**
- Create: `C:\dev\FEBio\jobs\0729C_CAE_local070_2p0mm\*`
- Create: `C:\dev\FEBio\jobs\0729C_CAE_local050_2p0mm\*`
- Create: `C:\dev\FEBio\jobs\0729C_CAE_local040_2p0mm\*`

**Interfaces:**
- Consumes: the parameterized builder and local sizes.
- Produces: three isolated FEB/build-report pairs that pass static and FEBio initialization gates.

- [ ] **Step 1: Build local070**

Run the builder with `--local-size 0.70` and its dedicated output directory.
Redirect stdout/stderr to `build.stdout.log` and `build.stderr.log`.

- [ ] **Step 2: Build local050**

Run the same command with `--local-size 0.50`.

- [ ] **Step 3: Build local040**

Run the same command with `--local-size 0.40`.

- [ ] **Step 4: Compare all three build reports**

Verify:

- identical STEP/reference hashes;
- identical required surface names and nonzero counts;
- identical screw stroke norm of `2.0 mm`;
- increasing local-region node/element density as local size decreases;
- G8 invalid count zero in all cases;
- global target `2.0 mm` and transition `3.0 mm` in all cases.

- [ ] **Step 5: Run FEBio initialization preflight for each FEB**

Run FEBio 4.12 against each model and stop before nonlinear stepping only if
the CLI supports an initialization/diagnostic mode. Otherwise run the full
case and treat mesh-initialization success as the first runtime gate. No model
may continue after a Negative Jacobian message.

---

### Task 5: Solve the three cases sequentially

**Files:**
- Create per case: `.xplt`, `.log`, `.stdout.log`, `.stderr.log`

**Interfaces:**
- Consumes: the three generated FEB files.
- Produces: three completed XPLT results or a retained failure package with exact diagnostics.

- [ ] **Step 1: Run local070**

Run:

```powershell
& 'C:\Program Files\FEBioStudio\bin\febio4.exe' -i '<local070 FEB>' -o '<local070 XPLT>'
```

Capture stdout/stderr. Verify normal termination, 11 completed steps or final
time `t = 1.0`, no Negative Jacobian, and a nonempty XPLT.

- [ ] **Step 2: Run local050**

Run only after local070 exits. Apply the same verification gates.

- [ ] **Step 3: Run local040**

Run only after local050 exits. Apply the same verification gates. Monitor peak
memory and retain logs if the process approaches the 32 GB machine limit.

---

### Task 6: Extract results, compare convergence, and hand off

**Files:**
- Reuse: `tmp/extract_xplt_comparison.py`
- Create: `reports/2026-07-29_bottom-frame-0729c-local-refinement-data.json`
- Create: `reports/2026-07-29_bottom-frame-0729c-local-refinement.html`
- Create: `C:\dev\FEBio\jobs\0729C_CAE_local_refinement_2p0mm\run-summary.json`

**Interfaces:**
- Consumes: three successful XPLTs, logs, and build reports.
- Produces: common final-state stress/displacement statistics, a browser-readable
  comparison report, and an absolute-path handoff.

- [ ] **Step 1: Read all XPLTs with official FBS**

Extract final-state ABS-domain effective stress maximum, p99.9, p99, mean, and
all-node displacement maximum/p99.9.

- [ ] **Step 2: Merge solver and mesh evidence**

Add Tet10/node counts, curved-node corrections, elapsed time, peak memory,
warnings, STEP/FEB/XPLT hashes, and final time to the summary JSON.

- [ ] **Step 3: Evaluate mesh convergence**

Calculate adjacent-case percentage differences for p99, p99.9, maximum stress,
and maximum displacement. Report global stabilization separately from
single-element peak stabilization.

- [ ] **Step 4: Generate the HTML comparison report**

Create a self-contained UTF-8 HTML report containing:

- the revised STEP identity and hash;
- the common global mesh size and 3.0 mm transition distance;
- each case's local mesh size and rigid-screw axial stroke (`2.0 mm`);
- Tet10/node counts, mesh-quality metrics, runtime, peak memory, and warnings;
- final stress/displacement statistics and adjacent-case percentage changes;
- a local-refinement preview and plots or tables sufficient to compare cases;
- absolute paths to each FEB, XPLT, solver log, and build report;
- a conclusion that distinguishes stable percentile results from
  mesh-sensitive single-element maxima.

Open the report in a permitted browser, verify that local images/tables render
and no horizontal overflow or missing-file links are present, then retain a
rendered screenshot as visual-QA evidence.

- [ ] **Step 5: Run final verification**

Run:

```powershell
& 'C:\Users\backo\OneDrive\Documents\FEBio\.worktrees\febio-gmsh-launcher\.venv\Scripts\python.exe' `
  -m pytest -q
```

Then verify every advertised FEB/XPLT/log/report path exists, every successful
log contains normal termination, and every file hash matches the comparison
JSON.

- [ ] **Step 6: Deliver the three model/result locations**

List one FEB, XPLT, solver log, and build report per case, followed by the
comparison JSON, comparison HTML, visual-QA screenshot, and a short
recommendation identifying the practical local mesh size.
