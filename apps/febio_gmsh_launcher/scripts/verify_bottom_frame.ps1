$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path (Split-Path -Parent $ProjectRoot) ".venv\Scripts\python.exe"
$Reference = "C:\dev\FEBio\WORKDIRECTORY\02_Bottom_Frame_FEBio_Tet10.feb"
$Config = Join-Path $ProjectRoot "config\examples\02_Bottom_Frame_FEBio_Tet10.gmsh-run.json"
$Output = "C:\dev\FEBio\gmsh-launcher-validation"

& $Python (Join-Path $PSScriptRoot "verify_bottom_frame.py") `
    --reference-feb $Reference `
    --config $Config `
    --output-dir $Output
exit $LASTEXITCODE
