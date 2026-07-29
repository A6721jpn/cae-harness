# M2 Rigid Indenter Contact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three part-surface prescribed displacements in `C:\dev\FEBio\jobs\02_Bottom_Frame,0729_CAE.feb` with a load-controller-driven rigid M2 indenter and frictionless sliding contact.

**Architecture:** Add one rigid HEX8 plate outside the existing planar contact face, use the existing 57-facet surface as the finely tessellated primary contact surface, and use the rigid plate face as the coarse secondary surface. Prescribe only the rigid body's motion; the part contact nodes retain both tangential degrees of freedom.

**Tech Stack:** FEBio specification 4.0 XML, FEBio 4.12, PowerShell/.NET XML validation, local FEBio source as the parameter/syntax reference.

## Global Constraints

- Overwrite only `C:\dev\FEBio\jobs\02_Bottom_Frame,0729_CAE.feb`.
- Create and hash-check a timestamped backup before editing.
- Preserve the ABS material, Tet10 mesh, fixed boundary conditions, solver tolerances, and output settings unless an added contact-output variable is required for validation.
- Use negative-normal resultant displacement 2.5 mm:
  `(-0.905630028638, 0.154563018450, 2.325068713943)` mm.
- Use one shared linear load controller from `(0,0)` to `(1,1)`.
- Use frictionless, tension-free, single-pass `sliding-elastic` contact.
- Do not change the `.fsm` model in this task.

---

### Task 1: Establish the failing precondition and protect the source file

**Files:**
- Read: `C:\dev\FEBio\jobs\02_Bottom_Frame,0729_CAE.feb`
- Create: `C:\dev\FEBio\jobs\02_Bottom_Frame,0729_CAE.before-rigid-contact-<timestamp>.feb`

**Interfaces:**
- Consumes: the current job FEB.
- Produces: an immutable backup path and SHA-256 value used by the final verification.

- [ ] **Step 1: Run the precondition test and confirm the physical-model defect exists**

Run a .NET XML assertion that requires exactly three boundary conditions named
`PushByScrew_X`, `PushByScrew_Y`, and `PushByScrew_Z`, and also requires zero
rigid materials and zero contacts. This test must pass before editing; otherwise
stop because the source model has changed.

- [ ] **Step 2: Confirm no FEBio process is using the target**

Query `Win32_Process` for `febio4.exe` command lines containing the exact target
path. Abort the edit when a matching process exists.

- [ ] **Step 3: Create the backup and verify its hash**

Use `Copy-Item -LiteralPath`, then calculate SHA-256 on source and backup. Abort
unless both hashes are identical.

### Task 2: Add the rigid indenter mesh, material, and domain

**Files:**
- Modify: `C:\dev\FEBio\jobs\02_Bottom_Frame,0729_CAE.feb`

**Interfaces:**
- Consumes: node IDs through 161411 and element IDs through 109712.
- Produces: material `M2_Screw_Rigid`, element set/domain
  `M2_Screw_Indenter`, and surface `M2_Screw_Contact`.

- [ ] **Step 1: Add rigid material ID 2**

Insert:

```xml
<material id="2" name="M2_Screw_Rigid" type="rigid body">
  <density>1</density>
  <E>1</E>
  <v>0</v>
</material>
```

- [ ] **Step 2: Add eight plate nodes**

Append node IDs 161412 through 161419 to the existing `Nodes` block:

```text
161412  17.852429024994,17.658811824169,15.078678329109
161413  18.424432953789,21.010350501469,15.078678329109
161414  21.541456042654,20.478371125832,16.328144217950
161415  20.969452113858,17.126832448531,16.328144217950
161416  17.924879427285,17.646446782693,14.892672831993
161417  18.496883356080,20.997985459993,14.892672831993
161418  21.613906444945,20.466006084356,16.142138720835
161419  21.041902516150,17.114467407055,16.142138720835
```

- [ ] **Step 3: Add one positive-orientation HEX8**

Insert after the Tet10 element block:

```xml
<Elements type="hex8" name="M2_Screw_Indenter">
  <elem id="109713">161412,161413,161414,161415,161416,161417,161418,161419</elem>
</Elements>
```

- [ ] **Step 4: Add the rigid contact face and surface pair**

Insert after the existing surfaces:

```xml
<Surface name="M2_Screw_Contact">
  <quad4 id="1">161412,161415,161414,161413</quad4>
</Surface>
<SurfacePair name="M2_Screw_Pair">
  <primary>NormalDisplacement2</primary>
  <secondary>M2_Screw_Contact</secondary>
</SurfacePair>
```

The reversed bottom-face order gives the rigid face normal opposite to the
part-face normal.

- [ ] **Step 5: Assign the rigid domain**

Append:

```xml
<SolidDomain name="M2_Screw_Indenter" mat="M2_Screw_Rigid"/>
```

to `MeshDomains`.

### Task 3: Replace the part BC with contact and rigid-body motion

**Files:**
- Modify: `C:\dev\FEBio\jobs\02_Bottom_Frame,0729_CAE.feb`

**Interfaces:**
- Consumes: material ID 2, `M2_Screw_Pair`, and the vector from Global Constraints.
- Produces: `M2_Screw_Frictionless`, three rigid translations, one rigid rotation lock, and load controller 1.

- [ ] **Step 1: Remove the three direct part-surface BCs**

Delete the complete `PushByScrew_X`, `PushByScrew_Y`, and `PushByScrew_Z`
elements from `Step01/Boundary`. Remove the empty step-local `Boundary`
container.

- [ ] **Step 2: Add the frictionless contact**

Add a top-level `Contact` section before `Step`:

```xml
<Contact>
  <contact name="M2_Screw_Frictionless" type="sliding-elastic" surface_pair="M2_Screw_Pair">
    <laugon>0</laugon>
    <tolerance>0.1</tolerance>
    <gaptol>0</gaptol>
    <penalty>1</penalty>
    <auto_penalty>1</auto_penalty>
    <update_penalty>0</update_penalty>
    <two_pass>0</two_pass>
    <knmult>0</knmult>
    <search_tol>0.01</search_tol>
    <symmetric_stiffness>0</symmetric_stiffness>
    <search_radius>1</search_radius>
    <seg_up>0</seg_up>
    <tension>0</tension>
    <minaug>0</minaug>
    <maxaug>10</maxaug>
    <node_reloc>0</node_reloc>
    <fric_coeff>0</fric_coeff>
    <smooth_aug>0</smooth_aug>
    <flip_primary>0</flip_primary>
    <flip_secondary>0</flip_secondary>
    <offset>0</offset>
  </contact>
</Contact>
```

- [ ] **Step 3: Add step-local rigid constraints**

Add to `Step01`:

```xml
<Rigid>
  <rigid_bc name="M2_Screw_FixRotations" type="rigid_fixed">
    <rb>2</rb>
    <Rx_dof>0</Rx_dof>
    <Ry_dof>0</Ry_dof>
    <Rz_dof>0</Rz_dof>
    <Ru_dof>1</Ru_dof>
    <Rv_dof>1</Rv_dof>
    <Rw_dof>1</Rw_dof>
  </rigid_bc>
  <rigid_bc name="PushByScrew_Rigid_X" type="rigid_displacement">
    <rb>2</rb><dof>x</dof><value lc="1">-0.905630028638</value><relative>0</relative>
  </rigid_bc>
  <rigid_bc name="PushByScrew_Rigid_Y" type="rigid_displacement">
    <rb>2</rb><dof>y</dof><value lc="1">0.154563018450</value><relative>0</relative>
  </rigid_bc>
  <rigid_bc name="PushByScrew_Rigid_Z" type="rigid_displacement">
    <rb>2</rb><dof>z</dof><value lc="1">2.325068713943</value><relative>0</relative>
  </rigid_bc>
</Rigid>
```

- [ ] **Step 4: Add the referenced load controller**

Insert after `Step`:

```xml
<LoadData>
  <load_controller id="1" name="PushByScrew_Ramp" type="loadcurve">
    <interpolate>LINEAR</interpolate>
    <extend>CONSTANT</extend>
    <points>
      <pt>0,0</pt>
      <pt>1,1</pt>
    </points>
  </load_controller>
</LoadData>
```

### Task 4: Verify structure, geometry, and solver behavior

**Files:**
- Read: `C:\dev\FEBio\jobs\02_Bottom_Frame,0729_CAE.feb`
- Create: `C:\dev\FEBio\debug-rigid-contact-0729\*`

**Interfaces:**
- Consumes: the edited FEB and backup hash.
- Produces: geometry assertions, headless FEBio logs, final file hash, and a bounded smoke-test result.

- [ ] **Step 1: Parse XML and assert exact object counts**

Require two materials, two element blocks, four surfaces, one surface pair, one
contact, four rigid BCs, one referenced load controller, and zero direct
`PushByScrew_X/Y/Z` part boundary conditions.

- [ ] **Step 2: Validate the vector and rigid element**

Compute:

- resultant displacement magnitude = 2.5 mm within `1e-9`;
- dot product with the positive normal = -2.5 mm within `1e-9`;
- HEX8 center Jacobian determinant greater than zero;
- rigid bottom-face normal dot part normal less than -0.999999;
- every `NormalDisplacement2` node projects inside the rigid quad with at least
  0.1 mm in-plane margin.

- [ ] **Step 3: Run a bounded headless FEBio smoke test**

Copy the edited FEB into a unique file under
`C:\dev\FEBio\debug-rigid-contact-0729`, run
`C:\Program Files\FEBioStudio\bin\febio4.exe -i <copy>` hidden, and monitor the
solver log. Stop only the exact verification PID after either:

- at least one time step converges and the next begins; or
- an error is logged.

- [ ] **Step 4: Inspect solver evidence**

Fail verification if the logs contain:

- `Negative jacobian`;
- `Model initialization failed`;
- `unreferenced load controllers`;
- an invalid rigid material/body/DOF message;
- an invalid or empty contact pair warning;
- a contact-surface projection error.

- [ ] **Step 5: Run final verification**

Re-run the XML and geometry assertions against the actual overwritten job FEB,
confirm the verification process is no longer running, report both source and
backup SHA-256 hashes, and state explicitly whether the bounded run is only a
smoke test or a complete solve.
