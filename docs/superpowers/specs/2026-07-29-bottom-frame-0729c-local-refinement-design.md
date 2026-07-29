# Bottom Frame 0729C Local-Refinement Analysis Design

## Purpose

Generate and solve three FEBio models from the revised Bottom Frame STEP while
concentrating mesh density on the lower curved arm identified by the user.
The three runs must isolate mesh density as the only varying analysis input.

## Authoritative inputs

- STEP:
  `C:\Users\backo\Downloads\02_Bottom Frame_v1.0,0729_C.step`
- STEP SHA-256:
  `2263F8913B9694A996B994772ED6FB45B1BFAF1A62DD850B77598E27FA2B57E6`
- Analysis-condition reference:
  `C:\dev\FEBio\jobs\0729B_CAE_Gmsh_2p22mm_fine075\02_Bottom_Frame_v1.00729_B_CAE_Gmsh_Tet10_2p22mm_fine075.feb`
- Refinement-region reference image:
  `C:\Users\backo\AppData\Local\Temp\codex-clipboard-5ed405a3-dcb5-401b-a07a-20dd7296d94a.png`

The STEP hash is a build precondition. The build must fail before writing an
FEB file if the source hash changes.

## Analysis conditions

All three cases use the same material, constraints, contact, nonlinear solver
settings, load curve, output variables, and rigid-screw construction as the
completed reference model. The one intentional load change is:

- Rigid-screw axial push stroke: `2.0 mm`
- Direction: opposite the selected contact face's outward normal
- Rigid rotations: fixed
- Contact: `sliding-elastic`, `fric_coeff = 0`

The generated Cartesian rigid displacement must equal
`-2.0 * selected_surface_outward_normal`, and its norm must equal `2.0 mm`
within `1e-10 mm`.

## Mesh-density cases

The global target size is `2.0 mm` for all three cases. Only the local target
size changes:

| Case | Global target | Local target | Transition distance |
|---|---:|---:|---:|
| Local 070 | 2.0 mm | 0.70 mm | 3.0 mm |
| Local 050 | 2.0 mm | 0.50 mm | 3.0 mm |
| Local 040 | 2.0 mm | 0.40 mm | 3.0 mm |

Use the same Gmsh tetrahedral algorithm, Tet10 ordering conversion, selection
transfer, and curved-midnode quality repair for every case.

## Refinement-region definition

The refinement target is the complete lower curved arm shown inside the user's
red outline:

- include the upper-left free end of the lower arm;
- include the complete curved span and its inside and outside radii;
- include the narrow inner hook/spring geometry lying inside the same lower
  curved assembly;
- include the right-bottom terminal block;
- exclude the upper housing, central frame, circular mounting hole, and upper
  hook/frame except where they fall inside the 3.0 mm transition band.

Implement the region as a Gmsh distance field seeded from the CAD faces of
this lower assembly. Apply a threshold field with `SizeMin` equal to the case's
local target, `SizeMax = 2.0 mm`, `DistMin = 0`, and `DistMax = 3.0 mm`.
Disable global curvature-driven refinement so it cannot silently refine the
rest of the model below the requested coarse target. CAD edges and points must
still be respected by the surface mesher.

Selection must use recorded geometric signatures rather than unstable STEP
face numbers. Record each seed face's centroid, bounding box, area, and CAD
entity tag in the build report. Generate a refinement-region preview image and
a mesh-size inventory so that accidental refinement of the upper body is
detectable.

## Build and output isolation

Create one output directory per mesh case:

- `C:\dev\FEBio\jobs\0729C_CAE_local070_2p0mm`
- `C:\dev\FEBio\jobs\0729C_CAE_local050_2p0mm`
- `C:\dev\FEBio\jobs\0729C_CAE_local040_2p0mm`

Each directory contains:

- the generated `.feb`;
- `build-report.json`;
- build stdout and stderr;
- FEBio stdout and stderr;
- FEBio `.log`;
- final `.xplt`;
- refinement-region preview;
- mesh-quality summary.

Existing job directories and completed artifacts must never be overwritten.

## Quality gates

The build fails closed unless all of the following hold:

- exactly one solid volume is imported;
- all required reference surfaces and the main domain are mapped;
- the selected screw-contact face is planar within `0.005 mm`;
- the rigid indenter covers the selected face with at least `0.05 mm` margin;
- every main-domain volume element is Tet10;
- the rigid indenter remains one Hex8 element;
- G8 Jacobian invalid count is zero after curved-midnode repair;
- no required surface is empty;
- local seed faces are nonempty and match the recorded lower-arm signature;
- the measured screw displacement norm is `2.0 mm`;
- source STEP and reference FEB hashes are recorded.

## Solver execution

Run the three cases sequentially to stay within the machine's approximately
32 GB physical-memory limit. A case proceeds to post-processing only after the
FEBio process exits and its log is checked.

Success requires:

- `N O R M A L   T E R M I N A T I O N`;
- 11 completed time steps or the equivalent complete final time `t = 1.0`;
- no Negative Jacobian message;
- a nonempty XPLT with a final state at `t = 1.0`.

Warnings remain reportable evidence and are not suppressed. If a case fails,
retain its FEB, logs, and build report, diagnose the cause, and do not relabel
it as a successful result.

## Result comparison

Read every completed XPLT with the official FEBio Studio FBS Python module.
For the deformable ABS domain, compare:

- effective-stress maximum, p99.9, p99, and mean;
- displacement-magnitude maximum and p99.9;
- Tet10 and node counts;
- corrected curved-midnode count and maximum correction;
- elapsed solver time and peak memory;
- convergence and material-iteration warnings.

Mesh convergence is supported only when distribution-level measures such as
p99 and p99.9 stabilize. A stable p99 with a moving single-element maximum is
reported as global stabilization with unresolved local peak convergence.

## Acceptance and handoff

The task is complete when three distinct FEB/XPLT pairs exist, all successful
runs pass the solver and Jacobian gates, and a comparison summary identifies
the practical mesh choice and any remaining mesh-sensitive peak. The handoff
lists absolute paths to every model, result, log, and comparison artifact.
