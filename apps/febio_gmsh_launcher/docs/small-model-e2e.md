# Small-model end-to-end validation

Validated on 2026-07-29 with:

- FEBio 4.12.0 at `C:\Program Files\FEBioStudio\bin\febio4.exe`
- Gmsh 4.15.2
- Python 3.12
- A 1 mm cube STEP, two independently transferred CAD Surface sets, one Part, a fixed bottom, and a prescribed normal displacement on the top

Command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\e2e\test_small_case.py -q -s
```

Observed result:

- Gmsh curved Tet10 elements: 897
- Minimum FEBio G8 `det(J)`: 0.0012550695966070953
- Invalid Tet10 elements after quality gate: 0
- FEBio equations: 4443
- FEBio time steps completed: 1
- FEBio peak memory: 49.5 MB
- FEBio termination: `N O R M A L   T E R M I N A T I O N`
- XPLT size: 143,784 bytes
- LOG size: 9,388 bytes

This test exercises the real route used by FEBio Studio: reference FEB selection scan, STEP/OCC face mapping, Gmsh high-order Tet10 generation, G8 quality gate, FEB translation, headless FEBio solve, and promotion to the original job basename.
