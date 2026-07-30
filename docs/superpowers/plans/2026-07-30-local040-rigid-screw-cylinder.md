# local040 Rigid Screw Cylinder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace only the `local040` rectangular rigid indenter with a diameter
2.0 mm, length 4.0 mm cylindrical rigid body and complete one new headless FEBio
run.

**Architecture:** A focused Python module creates and validates an oriented
linear-Tet4 cylinder mesh. A case builder parses the existing completed
`local040` FEB, preserves the deformable Tet10 model and all analysis sections,
replaces only the rigid nodes/elements/contact surface, and writes a new sibling
case directory. FEBio 4.12 then runs the new FEB and a validator records
geometry, connectivity, solver, result, and source-hash evidence.

**Tech Stack:** Python 3.13, NumPy 2.5.1, Gmsh 4.15.2, `xml.etree.ElementTree`,
pytest, FEBio 4.12 headless.

## Global Constraints

- Change and re-run `local040` only; never modify `local070` or `local050`.
- Cylinder diameter is `2.0 mm`; length is `4.0 mm`.
- Preserve the current `local040` deformable Tet10 nodes and elements.
- Preserve the current 2.0 mm Cartesian rigid displacement, contact, material,
  constraint, time-step, solver, load-curve, and output settings.
- Keep the original rectangular-indenter `local040` directory unchanged.
- Write the new case to
  `C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_2p0mm`.

---

### Task 1: Oriented rigid-cylinder mesh

**Files:**
- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/rigid_cylinder.py`
- Create: `febio_gmsh_launcher/tests/test_rigid_cylinder.py`

**Interfaces:**
- Produces:
  `build_cylinder_mesh(center, outward_normal, diameter_mm=2.0,
  length_mm=4.0, target_size_mm=0.35) -> CylinderMesh`
- `CylinderMesh` contains `points`, `tet4`, `contact_tri3`,
  `diameter_mm`, `length_mm`, and `axis`.
- Produces:
  `validate_cylinder_mesh(mesh: CylinderMesh) -> dict[str, object]`.

- [ ] **Step 1: Write failing geometry tests**

```python
def test_builds_positive_tet4_cylinder_with_contact_disk():
    mesh = build_cylinder_mesh(
        np.array([1.0, 2.0, 3.0]),
        np.array([0.0, 0.0, 1.0]),
    )
    report = validate_cylinder_mesh(mesh)
    assert report["invalid_tet4_count"] == 0
    assert report["measured_diameter_mm"] == pytest.approx(2.0, abs=0.005)
    assert report["measured_length_mm"] == pytest.approx(4.0, abs=0.005)
    assert report["contact_area_mm2"] == pytest.approx(np.pi, rel=0.02)


def test_orients_contact_end_at_center_and_extrudes_outward():
    center = np.array([5.0, -2.0, 7.0])
    outward = np.array([1.0, 2.0, -3.0])
    mesh = build_cylinder_mesh(center, outward)
    report = validate_cylinder_mesh(mesh)
    assert np.linalg.norm(report["contact_centroid"] - center) < 0.005
    assert np.dot(report["axis"], outward / np.linalg.norm(outward)) > 0.99999
    assert report["rim_chord_error_mm"] < 0.01
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest febio_gmsh_launcher/tests/test_rigid_cylinder.py -q
```

Expected: collection fails because `febio_gmsh_launcher.rigid_cylinder` does not
exist.

- [ ] **Step 3: Implement the minimal mesh generator**

Implement the immutable `CylinderMesh` dataclass, a stable orthonormal basis from
the outward normal, Gmsh OCC cylinder creation, linear Tet4 extraction, and
selection of the circular end whose centroid is nearest `center`. Reindex Gmsh
node tags to zero-based compact arrays. Reverse any contact triangle whose
normal does not face the deformable target. Validate signed Tet4 volumes,
diameter, length, end centroid, contact area, axis alignment, and chord error.

- [ ] **Step 4: Run focused and launcher tests**

```powershell
python -m pytest febio_gmsh_launcher/tests/test_rigid_cylinder.py -q
python -m pytest febio_gmsh_launcher/tests -q
```

Expected: all focused tests pass; existing suite remains green.

- [ ] **Step 5: Commit Task 1**

```powershell
git add febio_gmsh_launcher/src/febio_gmsh_launcher/rigid_cylinder.py `
  febio_gmsh_launcher/tests/test_rigid_cylinder.py
git commit -m "feat: generate oriented rigid cylinder mesh"
```

### Task 2: Preserve the FEB and replace only the rigid indenter

**Files:**
- Create: `febio_gmsh_launcher/scripts/build_local040_cylinder.py`
- Create: `febio_gmsh_launcher/tests/test_build_local040_cylinder.py`

**Interfaces:**
- Consumes: `build_cylinder_mesh()` and `validate_cylinder_mesh()` from Task 1.
- Produces:
  `replace_rigid_indenter(source_feb: Path, output_feb: Path) -> dict`.
- CLI arguments:
  `--source-feb`, `--output-dir`, and optional `--febio-exe`.

- [ ] **Step 1: Write failing FEB replacement tests**

Create a compact FEB fixture in the test with one deformable Tet10 element,
one rigid Hex8 element, `M2_Screw_Contact`, `NormalDisplacement2`, the material
domains, surface pair, rigid BCs, and load curve. Assert:

```python
report = replace_rigid_indenter(source, output)
assert deformable_signature(output) == deformable_signature(source)
assert report["removed_rigid_hex8"] == 1
assert report["rigid_tet4"] > 0
assert report["rigid_contact_tri3"] > 0
assert report["displacement_magnitude_mm"] == pytest.approx(2.0)
assert all_references_resolve(output)
```

Also assert that the source file hash does not change.

- [ ] **Step 2: Run the focused test and verify RED**

```powershell
python -m pytest febio_gmsh_launcher/tests/test_build_local040_cylinder.py -q
```

Expected: import fails because `build_local040_cylinder.py` does not exist.

- [ ] **Step 3: Implement minimal XML replacement**

Parse the source FEB. Compute the area-weighted centroid and best-fit plane of
`NormalDisplacement2`; choose the outward sign consistent with the existing
rigid displacement. Remove only nodes referenced by
`M2_Screw_Indenter`, its Hex8 `Elements` block, and the old
`M2_Screw_Contact`. Append cylinder nodes above the deformable maximum node ID,
append Tet4 elements above the deformable maximum element ID, and add the
contact-end Tri3 surface. Preserve every other XML element and value. Write:

- the revised FEB;
- `build-report.json`;
- `provenance.json` with SHA-256 hashes for source and output;
- `model-validation.json` with reference-resolution, deformable-signature, and
  cylinder checks.

- [ ] **Step 4: Run focused and full tests**

```powershell
python -m pytest febio_gmsh_launcher/tests/test_build_local040_cylinder.py -q
python -m pytest febio_gmsh_launcher/tests -q
```

Expected: all tests pass.

- [ ] **Step 5: Build the real revised case and inspect validation**

```powershell
python febio_gmsh_launcher/scripts/build_local040_cylinder.py `
  --source-feb "C:\dev\FEBio\jobs\0729C_CAE_local040_2p0mm\02_Bottom_Frame_v1.0,0729_CAE_Gmsh_Tet10_2p0mm_local040.feb" `
  --output-dir "C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_2p0mm"
```

Expected: exit `0`; `model-validation.json` reports no invalid Tet4, unchanged
deformable signature, 2.0 mm diameter, 4.0 mm length, and resolved references.

- [ ] **Step 6: Commit Task 2**

```powershell
git add febio_gmsh_launcher/scripts/build_local040_cylinder.py `
  febio_gmsh_launcher/tests/test_build_local040_cylinder.py
git commit -m "feat: replace local040 indenter with M2 cylinder"
```

### Task 3: Headless solve and final evidence

**Files:**
- Modify: `febio_gmsh_launcher/scripts/build_local040_cylinder.py`
- Modify: `febio_gmsh_launcher/tests/test_build_local040_cylinder.py`
- Create outside Git:
  `C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_2p0mm\solve-summary.json`

**Interfaces:**
- Produces:
  `run_febio(case_dir: Path, febio_exe: Path) -> dict[str, object]`.
- Produces solver files `normal-solve.xplt`, `normal-solve.log`,
  `normal-solve.stdout.log`, and `normal-solve.stderr.log`.

- [ ] **Step 1: Write failing solver-summary test**

Use a temporary executable script that returns a chosen exit code and emits a
short FEBio-style completion log. Assert that `run_febio()` records the exact
command, executable hash, input hash, exit code, elapsed time, log path, XPLT
path, and rejects a zero-byte or missing XPLT.

- [ ] **Step 2: Run the focused test and verify RED**

```powershell
python -m pytest febio_gmsh_launcher/tests/test_build_local040_cylinder.py -q
```

Expected: fail because `run_febio()` is not implemented.

- [ ] **Step 3: Implement the headless runner**

Run:

```text
C:\Program Files\FEBioStudio\bin\febio4.exe
  -i <revised-local040.feb>
  -o normal-solve.log
  -p normal-solve.xplt
```

Capture stdout/stderr without opening FEBio Studio. Record timestamps and hashes
in `solve-summary.json`. Treat nonzero exit, missing XPLT, negative Jacobian,
model initialization failure, or final time below `1.0` as failure.

- [ ] **Step 4: Run tests and the real local040 solve**

```powershell
python -m pytest febio_gmsh_launcher/tests -q
python febio_gmsh_launcher/scripts/build_local040_cylinder.py `
  --source-feb "C:\dev\FEBio\jobs\0729C_CAE_local040_2p0mm\02_Bottom_Frame_v1.0,0729_CAE_Gmsh_Tet10_2p0mm_local040.feb" `
  --output-dir "C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_2p0mm" `
  --run `
  --febio-exe "C:\Program Files\FEBioStudio\bin\febio4.exe"
```

Expected: tests pass; FEBio exit code `0`; final time `1.0`; nonempty readable
XPLT; no negative Jacobian or initialization error.

- [ ] **Step 5: Verify untouched cases and acceptance criteria**

Recalculate the original `local070` and `local050` FEB/XPLT hashes and compare
them with the pre-build provenance snapshot. Re-run the model validator on the
written FEB, scan the complete solver log for errors, and record the final
2.0 mm rigid displacement from the prescribed values and load curve.

- [ ] **Step 6: Commit Task 3**

```powershell
git add febio_gmsh_launcher/scripts/build_local040_cylinder.py `
  febio_gmsh_launcher/tests/test_build_local040_cylinder.py
git commit -m "feat: run and validate cylindrical local040 case"
```
