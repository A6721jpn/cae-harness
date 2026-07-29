# M2 rigid-indenter contact design

## Goal

Update `C:\dev\FEBio\jobs\02_Bottom_Frame,0729_CAE.feb` so that the surface
currently named `NormalDisplacement2` is pushed 2.5 mm in its negative-normal
direction by an M2 screw idealization while remaining free to slide in both
tangential directions.

The source FEB must be backed up before it is overwritten.

## Existing model evidence

- `NormalDisplacement2` contains 57 quadratic triangular facets and 136 nodes.
- It is effectively planar: the measured normal-coordinate spread is about
  0.000002 mm.
- Its area is 5.8438 mm2 and its equivalent circular diameter is 2.7277 mm.
- Its positive unit normal is
  `(0.362252011455, -0.061825207380, -0.930027485577)`.
- The existing `PushByScrew_X`, `PushByScrew_Y`, and `PushByScrew_Z` boundary
  conditions prescribe all three translational degrees of freedom of the part
  surface and therefore suppress tangential slip.

## Considered approaches

1. **Rigid indenter plus frictionless sliding contact — selected.**
   This models unilateral compression, permits separation, permits tangential
   slip, and allows the screw motion to be ramped by a load controller.
2. **Full M2 screw CAD and contact.**
   This adds unnecessary geometry and meshing cost when only the flat pushing
   interface and global structural response are required.
3. **Fixed-normal-displacement constraint with nonzero right-hand side.**
   This is simpler, but it is bilateral, forces every selected node to the same
   normal offset, and does not provide a load-controller-driven right-hand side.

## Selected geometry

Add one rigid HEX8 element named `M2_Screw_Indenter`:

- contact-face center:
  `(19.696942533824, 19.068591475000, 15.703411273529)` mm;
- contact-face size: 3.4 mm by 3.4 mm, which covers the selected part surface;
- thickness: 0.2 mm in the positive surface-normal direction;
- bottom face initially coincident with `NormalDisplacement2`;
- bottom-face outward normal points toward the deformable part surface.

The rigid contact surface will be named `M2_Screw_Contact`.

## Materials and domains

- Preserve the existing ABS material and Tet10 domain.
- Add material ID 2, `M2_Screw_Rigid`, of type `rigid body`.
- Add the new HEX8 element as a solid domain using the rigid material.
- The rigid body center of mass is calculated automatically.

## Contact

Create surface pair `M2_Screw_Pair`:

- primary: `NormalDisplacement2`;
- secondary: `M2_Screw_Contact`.

Add one `sliding-elastic` interaction:

- `fric_coeff = 0`;
- `tension = 0`;
- `two_pass = 0`;
- `auto_penalty = 1`;
- `penalty = 1`;
- `laugon = 0` initially;
- `symmetric_stiffness = 0`;
- `search_radius = 1` mm;
- no node relocation.

The deformable surface receives no direct prescribed displacement.

## Rigid-body motion

Fix all three rigid-body rotations. Prescribe all three global translations
with the same linear load controller from `(0,0)` to `(1,1)`:

- X: `-0.905630028638` mm;
- Y: `+0.154563018450` mm;
- Z: `+2.325068713943` mm.

Their resultant is 2.5 mm and their dot product with the positive surface
normal is -2.5 mm. The rigid motion is absolute (`relative = 0`).

## Verification

The updated FEB is accepted only if all of the following hold:

1. The XML is well formed and all added IDs and names are unique.
2. The rigid HEX8 element has a positive Jacobian and its contact face covers
   all nodes of `NormalDisplacement2` in plane.
3. The two contact-surface normals face one another.
4. No `PushByScrew_X/Y/Z` part-surface boundary condition remains.
5. The three rigid displacement values reference exactly one shared load
   controller.
6. FEBio 4.12 initializes without negative Jacobians, unreferenced load
   controllers, invalid contact pairs, or rigid-body degree-of-freedom errors.
7. A bounded headless run enters the nonlinear solve and is stopped after
   sufficient contact evidence is produced; a complete long solve is not
   required for the smoke test.

## Scope limit

This is a rigid flat-ended screw idealization. It does not model screw threads,
thread preload, head/tip curvature, or friction. Those require measured contact
geometry and friction/preload inputs.
