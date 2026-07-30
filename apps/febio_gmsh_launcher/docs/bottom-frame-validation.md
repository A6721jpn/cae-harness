# Bottom Frame validation

Validated on 2026-07-29 against:

- Reference FEB: `C:\dev\FEBio\WORKDIRECTORY\02_Bottom_Frame_FEBio_Tet10.feb`
- STEP: `C:\Users\backo\Downloads\02_Bottom Frame_v1.0,0728_C.step`
- STEP SHA-256: `2C6BA30347D859E1306767137D8D51AA4F23200E864319D8E45E46BF873DABB6`
- Gmsh 4.15.2
- FEBio 4.12.0

Command:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify_bottom_frame.ps1
```

Results:

- STEP import: 1 closed OCC volume, 660 CAD faces
- Reference selection mapping:
  - `PushByScrew` → OCC face 101
  - `ZeroDisplacement1` → OCC face 63
  - `Part1` → the single OCC volume
- Gmsh curved Tet10: 123,034 elements
- Nodes: 220,513
- `PushByScrew` Tri6 faces: 68
- `ZeroDisplacement1` Tri6 faces: 1,724
- Minimum FEBio G8 `det(J)`: 2.456041901969607e-06
- Minimum corner Tet4 volume: 4.11432484997988e-07 mm³
- Invalid Tet10 elements: 0
- Curvature-relaxed nodes: 0 (`alpha=1.0`)
- FEBio read: `SUCCESS!`
- FEBio initialization reached `beginning time step 1 : 0.1`
- Negative Jacobian during initialization: not present

The validation intentionally stops immediately after the first time step begins. This proves mesh/model initialization while avoiding the known high memory cost of completing the full nonlinear Bottom Frame solve.

Artifacts:

- Translated FEB: `C:\dev\FEBio\gmsh-launcher-validation\02_Bottom_Frame_Gmsh_Tet10.feb`
- Report: `C:\dev\FEBio\gmsh-launcher-validation\bottom-frame-report.json`
- Initialization console: `C:\dev\FEBio\gmsh-launcher-validation\febio-initialization-console.log`
- Translated FEB SHA-256: `C53AD6A5AD2609025C90681F8B7DE8A0205C629057B76EBE33816879392427EF`

The generated FEB retains `ZeroDisplacement1`, `PushByScrew`, and `Part1`, and the original BC/load references resolve to those names.
