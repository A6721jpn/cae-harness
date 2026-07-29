$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $ProjectRoot "dist\FEBioGmshLauncher"
$InstallRoot = Join-Path $env:LOCALAPPDATA "FEBioGmshLauncher"
$Target = Join-Path $InstallRoot "current"

if (-not (Test-Path -LiteralPath (Join-Path $Source "FEBioGmshLauncher.exe"))) {
    throw "Build output not found. Run scripts\build.ps1 first."
}
if (-not $InstallRoot.StartsWith($env:LOCALAPPDATA, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to install outside LOCALAPPDATA: $InstallRoot"
}

$Staging = Join-Path $InstallRoot ("staging-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Staging -Force | Out-Null
Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $Staging -Recurse -Force
}
if (-not (Test-Path -LiteralPath (Join-Path $Staging "FEBioGmshLauncher.exe"))) {
    throw "Staged installation is incomplete: $Staging"
}
if (Test-Path -LiteralPath $Target) {
    $Backup = Join-Path $InstallRoot ("previous-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    Move-Item -LiteralPath $Target -Destination $Backup
}
Move-Item -LiteralPath $Staging -Destination $Target

$Executable = Join-Path $Target "FEBioGmshLauncher.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "Installed executable is missing: $Executable"
}
Write-Output "Installed: $Executable"
Write-Output "FEBio Studio: Tools > Launch Configurations > Add > local"
Write-Output "FEBio executable: $Executable"
Write-Output 'Run command: keep the default -i $(Filename)'
