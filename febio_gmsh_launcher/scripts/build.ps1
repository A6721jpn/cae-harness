$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path (Split-Path -Parent $ProjectRoot) ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project virtual environment was not found: $Python"
}

Push-Location $ProjectRoot
try {
    & $Python -m pip install --disable-pip-version-check -e .
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --name FEBioGmshLauncher `
        --paths src `
        --collect-all gmsh `
        launcher_entry.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$Exe = Join-Path $ProjectRoot "dist\FEBioGmshLauncher\FEBioGmshLauncher.exe"
if (-not (Test-Path -LiteralPath $Exe)) {
    throw "Packaged launcher was not created: $Exe"
}
Write-Output $Exe
