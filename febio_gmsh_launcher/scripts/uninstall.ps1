$ErrorActionPreference = "Stop"

$InstallRoot = Join-Path $env:LOCALAPPDATA "FEBioGmshLauncher"
if (-not $InstallRoot.StartsWith($env:LOCALAPPDATA, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to remove path outside LOCALAPPDATA: $InstallRoot"
}
if (Test-Path -LiteralPath $InstallRoot) {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
    Write-Output "Removed: $InstallRoot"
}
else {
    Write-Output "Not installed: $InstallRoot"
}
