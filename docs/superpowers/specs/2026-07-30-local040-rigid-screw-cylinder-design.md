# local040 M2 rigid-screw cylinder design

## Objective

Replace only the rigid rectangular indenter in the completed `local040` Bottom
Frame model with a meshed cylindrical rigid body representing an M2 screw.
Re-run only this revised `local040` case. The `local070` and `local050` models,
results, logs, and reports remain unchanged.

## Source model and preservation

The source analysis is:

`C:\dev\FEBio\jobs\0729C_CAE_local040_2p0mm\02_Bottom_Frame_v1.0,0729_CAE_Gmsh_Tet10_2p0mm_local040.feb`

The original `local040` directory is retained as evidence of the earlier
rectangular-indenter run. The revised FEB, solver outputs, logs, validation
record, and provenance record are written to a new sibling directory named:

`C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_2p0mm`

Before modification, record SHA-256 hashes for the three original FEB files and
the existing `local070`, `local050`, and `local040` result files. After the new
run, verify that the `local070` and `local050` hashes have not changed.

## Cylinder geometry and placement

- Diameter: `2.0 mm`
- Length: `4.0 mm`
- Contact end: planar circular face
- Axis: aligned with the existing rigid-screw displacement axis
- Contact-end center: at the area centroid of the existing
  `NormalDisplacement2` target surface, projected onto its best-fit plane
- Initial position: the circular contact end is coplanar with the target plane
- Extrusion direction: away from the deformable ABS body

The displacement axis is the existing normalized vector opposite the target
surface outward normal. The current prescribed Cartesian displacement is
retained:

`(-0.724511843958, 0.123631294891, 1.860053195715) mm`

Its magnitude is `2.0 mm`.

## Rigid-cylinder mesh

Generate the cylinder with Gmsh as a linear Tet4 solid mesh. Use at least 24
segments around the circumference and a nominal element size no larger than
`0.35 mm` on the circular contact end. The roundness chord error at the contact
rim must be below `0.01 mm`.

The complete cylinder volume is assigned to the existing
`M2_Screw_Rigid` rigid-body material. Only the circular end facing the ABS body
is exported as `M2_Screw_Contact`; the cylindrical side and remote end are not
members of the contact surface.

The deformable body remains exactly the existing `local040` Tet10 mesh. Its node
coordinates, Tet10 connectivity, named surfaces, material, and mesh density are
not regenerated.

## Analysis conditions retained

Retain the current analysis conditions without change:

- ABS material definition
- `ZeroDisplacement1` and `ZeroDisplacement3` constraints
- `NormalDisplacement2` deformable contact surface
- `sliding-elastic` contact with `fric_coeff = 0`
- `M2_Screw_FixRotations`
- the three Cartesian rigid-displacement constraints
- `PushByScrew_Ramp`, linear from `(0, 0)` to `(1, 1)`
- final axial screw stroke of `2.0 mm`
- nonlinear solver and time-step settings
- requested plot variables

The existing rectangular Hex8 element and its eight rigid nodes are removed.
New rigid-cylinder node and element IDs start above the maximum deformable-body
node and element IDs so that all retained selections continue to resolve.

## Validation and acceptance

The revised model is acceptable when all of the following pass:

1. The FEB XML parses and all material, domain, surface-pair, boundary, rigid,
   load-controller, and output references resolve.
2. The ABS node coordinates and Tet10 connectivity are byte-for-byte equivalent
   to the source `local040` model after XML normalization.
3. The rigid geometry measures `2.0 mm` in diameter and `4.0 mm` in length
   within `0.005 mm`.
4. The circular contact surface area agrees with `pi mm2` within `2%`, its
   center lies on the target plane within `0.005 mm`, and its normal is aligned
   with the prescribed screw axis.
5. The cylinder has no invalid or non-positive-volume Tet4 elements.
6. FEBio model initialization completes without a negative Jacobian or initial
   contact-penetration failure.
7. The headless FEBio run reaches final time `1.0` and produces a readable XPLT.
8. The rigid-body final displacement magnitude is `2.0 mm` within solver output
   precision.
9. The original `local070` and `local050` FEB and XPLT hashes remain unchanged.

Solver completion and geometric checks establish numerical execution for this
model only. They do not validate the ABS material card, real screw-thread load
transfer, contact friction, manufacturing variation, or product strength.

## Deliverables

- Revised `local040` FEB with the cylindrical rigid screw
- New XPLT, solver log, stdout/stderr, and run summary
- Machine-readable cylinder and model validation report
- Provenance file containing source and output hashes
- A concise handoff identifying both the preserved original run and the revised
  cylinder run
