# FEBio Post von Mises and Rough Safety-Factor Contours

## Goal

Create a FEBio Studio post-processing project for the completed Bottom Frame
analysis that lets the user switch between:

- von Mises stress in MPa; and
- a rough safety factor based on a provisional 46.17 MPa strength.

The completed `.feb`, `.log`, and `.xplt` files remain unchanged.

## Source

- Plot database:
  `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.xplt`
- Analysis input:
  `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.feb`
- Analysis log:
  `C:\dev\FEBio\jobs\jobs\02_Bottom_Frame,0729_CAE.log`
- Main deformable body: `Part2`
- Rigid indenter: `M2_Screw_Indenter`

## Derived fields

### von_Mises_stress_MPa

Use FEBio Studio's effective-stress scalar derived from the stored stress
tensor. Display the main deformable body and exclude the rigid indenter from
engineering interpretation.

### rough_FOS_46p17MPa

Calculate:

`rough_FOS = 46.17 / max(von_Mises_stress_MPa, 0.01)`

The 0.01 MPa floor avoids division by zero. The stored result is a screening
quantity, not a validated safety factor. The contour range should default to
0 through 5 so values above 5 saturate visually and the important region near
1 remains readable. A value below 1 means the local von Mises stress exceeds
the provisional 46.17 MPa basis.

## Persistence

Save the configured result as a separate FEBio Studio post project beside the
completed result files. Do not overwrite the `.feb`, `.log`, or `.xplt`.

Use explicit field names containing `46p17MPa` so the provisional basis cannot
be mistaken for a material-qualified allowable.

## Verification

1. Reopen the saved post project in FEBio Studio.
2. Confirm the final state is `t = 1`.
3. Confirm the von Mises contour can be selected.
4. Confirm the rough safety-factor contour can be selected.
5. Confirm the main body is visible and the rigid indenter is excluded from
   interpretation.
6. Record the maximum von Mises stress and minimum rough safety factor and
   check that they satisfy `FOS_min = 46.17 / von_Mises_max`, subject to the
   0.01 MPa floor.

## Limitations

- 46.17 MPa is taken from the upper point of the provisional material curve,
  not from a manufacturer-qualified, molded-part design allowable.
- Von Mises stress does not by itself represent ABS fracture, creep,
  environmental stress cracking, weld-line weakness, or fatigue.
- Singular/contact-edge stress peaks must be interpreted with mesh-convergence
  and stress-averaging checks.
