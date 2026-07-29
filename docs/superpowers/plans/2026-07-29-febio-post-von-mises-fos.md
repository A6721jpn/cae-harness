# FEBio Post von Mises and Rough Safety-Factor Contours Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a reusable FEBio Studio post session for the completed Bottom Frame analysis with switchable von Mises stress and provisional 46.17 MPa safety-factor contours.

**Architecture:** Open the immutable completed `.xplt`, use FEBio Studio's native effective-stress field and math-data support to create the two scalar views, then save them in a separate `.fsps` post-session file. Reopen that session and verify the fields, final time, body visibility, reciprocal extrema, and source-file hashes.

**Tech Stack:** FEBio Studio 3.1 GUI, FEBio 4.12 plot database, Windows Computer Use, PowerShell SHA-256 verification

## Global Constraints

- Source plot database: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.xplt`.
- Source `.feb`, `.log`, and `.xplt` must remain byte-for-byte unchanged.
- Main deformable body is `Part2`; `M2_Screw_Indenter` is a rigid contact tool.
- Strength basis is the provisional 46.17 MPa curve value.
- Safety-factor formula is `46.17 / max(von_Mises_stress_MPa, 0.01)`.
- Default safety-factor legend range is 0 through 5.
- Output is `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_VM_FOS.fsps`.

---

### Task 1: Protect the completed analysis artifacts

**Files:**
- Read: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.feb`
- Read: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.log`
- Read: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.xplt`
- Create: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_VM_FOS.source-hashes.txt`

**Interfaces:**
- Consumes: the three completed analysis artifacts.
- Produces: baseline SHA-256 hashes used by Task 4.

- [ ] **Step 1: Record file sizes, timestamps, and SHA-256 hashes**

Run:

```powershell
Get-Item -LiteralPath @(
  'C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.feb',
  'C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.log',
  'C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.xplt'
)
Get-FileHash -Algorithm SHA256 -LiteralPath @(
  'C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.feb',
  'C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.log',
  'C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.xplt'
)
```

Expected: all three exist; the log ends in `NORMAL TERMINATION`.

- [ ] **Step 2: Save the baseline evidence**

Write the exact paths, sizes, timestamps, and hashes to
`02_Bottom_Frame,0729_CAE_VM_FOS.source-hashes.txt` without changing the
source files.

### Task 2: Configure native von Mises stress display

**Files:**
- Read: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.xplt`
- Create: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_VM_FOS.fsps`

**Interfaces:**
- Consumes: the completed plot database and native stress tensor.
- Produces: a post session with the final state and effective-stress contour.

- [ ] **Step 1: Open the completed plot database**

Start FEBio Studio and open the `.xplt`. Wait for the post model and all time
states to finish loading.

- [ ] **Step 2: Select the final state**

Set the time/state control to the final converged state `t = 1`.

- [ ] **Step 3: Select effective stress**

Choose the scalar stress representation named `effective stress` or the
version-equivalent FEBio Studio von Mises option. Confirm a scalar contour is
visible on `Part2`.

- [ ] **Step 4: Save the initial post session**

Save As
`C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_VM_FOS.fsps`.

### Task 3: Add the provisional safety-factor field

**Files:**
- Modify: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_VM_FOS.fsps`

**Interfaces:**
- Consumes: FEBio Studio's effective-stress scalar.
- Produces: scalar field `rough_FOS_46p17MPa` and a 0-to-5 contour view.

- [ ] **Step 1: Add a math-data scalar**

Use the Post Data field creation UI to add a math field named
`rough_FOS_46p17MPa`. Reference the effective-stress scalar with the exact
identifier exposed by the installed FEBio Studio version and enter the
equivalent of:

```text
46.17 / max(effective_stress, 0.01)
```

- [ ] **Step 2: Set the legend range**

Set manual range minimum `0` and maximum `5`. Do not clamp the stored field;
only saturate the display legend.

- [ ] **Step 3: Restrict interpretation to the main body**

Hide `M2_Screw_Indenter` or otherwise exclude it from contour interpretation.
Keep `Part2` visible.

- [ ] **Step 4: Save the updated session**

Save the `.fsps` and confirm the file timestamp and size change.

### Task 4: Reopen and verify the deliverable

**Files:**
- Read: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_VM_FOS.fsps`
- Read: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_VM_FOS.source-hashes.txt`
- Create: `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE_VM_FOS.verification.txt`

**Interfaces:**
- Consumes: saved session, baseline hashes, and the completed source files.
- Produces: reopen evidence, contour extrema, and source-integrity evidence.

- [ ] **Step 1: Close and reopen the post session**

Open the `.fsps` in FEBio Studio. Confirm it resolves the source `.xplt`
without prompting for a missing file.

- [ ] **Step 2: Verify the final state and von Mises field**

Confirm `t = 1`, select the effective-stress contour, and record the displayed
maximum for `Part2`.

- [ ] **Step 3: Verify the safety-factor field**

Select `rough_FOS_46p17MPa`, confirm the legend spans 0 through 5, and record
the minimum on `Part2`.

- [ ] **Step 4: Check reciprocal extrema**

Calculate `46.17 / von_Mises_max`. The result must agree with the recorded
minimum safety factor to the precision allowed by nodal projection and legend
reporting. If FEBio Studio evaluates the derived field at a different data
class or projection stage, record that difference rather than asserting exact
equality.

- [ ] **Step 5: Recheck the source hashes**

Run `Get-FileHash -Algorithm SHA256` on the three completed analysis artifacts
and compare all hashes with the baseline.

- [ ] **Step 6: Save verification evidence**

Write the final state, selectable field names, extrema, legend range,
visibility state, source hash comparison, FEBio Studio version, and any
projection caveat to
`02_Bottom_Frame,0729_CAE_VM_FOS.verification.txt`.
