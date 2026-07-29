# FEBio Gmsh Tet10 Launcher

This Windows launcher is used as a FEBio Studio 3.1 **Local Launch Configuration**. It keeps CAD-based Surface/Part selections from the reference FEB, remeshes the configured STEP with curved Gmsh Tet10 elements, applies a FEBio G8 Jacobian gate, runs FEBio 4.12 headlessly, and returns the final XPLT under the original Studio job name.

## Build and install

```powershell
cd C:\path\to\FEBio\febio_gmsh_launcher
..\.venv\Scripts\python.exe -m pip install -e .
.\scripts\build.ps1
.\scripts\install.ps1
```

Use the path printed by `install.ps1` in:

1. FEBio Studio → Tools → Launch Configurations.
2. Add a `local` configuration.
3. Set `FEBio executable` to `FEBioGmshLauncher.exe`.
4. In the Run dialog, keep the default command `-i $(Filename)`.

## Model-side configuration

Place `<job-name>.gmsh-run.json` beside the exported FEB, or in one of its first three parent folders:

```json
{
  "schema_version": 1,
  "model_stem": "Model",
  "step_path": "C:\\CAD\\Model.step",
  "step_sha256": "64 lowercase hexadecimal characters",
  "gmsh": {
    "target_size_mm": 2.0,
    "min_size_mm": 0.2,
    "algorithm3d": 10,
    "curvature_elements_per_2pi": 20
  },
  "quality": {
    "min_det_j": 0.0,
    "min_alpha": 0.0,
    "max_corrected_fraction": 0.01,
    "max_displacement_mm": 1.0
  },
  "febio_exe": "C:\\Program Files\\FEBioStudio\\bin\\febio4.exe"
}
```

The SHA-256 check prevents running against a STEP revision different from the one used to define the mapping. A preflight dialog freezes the current FEB/STEP/settings snapshot.

## Outputs and diagnostics

Each run creates `<job>.gmsh-runs\<timestamp-id>\` containing the translated FEB, launcher log, FEBio log/XPLT, and `run-report.json`. On success, the launcher atomically promotes the log and XPLT to `<job>.log` and `<job>.xplt`; FEBio Studio then opens the expected result. Existing outputs are moved to the run folder's `previous` directory before solving, so a failed run cannot open a stale result as if it were new.

The initial schema transfers CAD Surface sets and one Part/volume. Node, edge, vertex, discrete-node, shell, and multi-volume ambiguous selections fail before meshing.

For unattended verification:

```powershell
FEBioGmshLauncher.exe -i Model.feb --non-interactive
```

Exit codes are 20 config, 21 cancelled preflight, 30 selection transfer, 40 Tet10 quality, 50 solver, and 70 internal error.

## Uninstall

```powershell
.\scripts\uninstall.ps1
```
