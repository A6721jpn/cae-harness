# FEBio CAE Harness Phase 1E BottomFrame Real-model E2E Acceptance Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: Use `superpowers:executing-plans` for this acceptance run, `superpowers:systematic-debugging` for any unexpected result, and `superpowers:verification-before-completion` before reporting success. This plan operates on a real CAE case and therefore does not use subagent code edits or Git commits while the real solve is active.

**Goal:** Prove that the installed, released Phase 1 harness can take the exact reviewed BottomFrame aligned rigid-screw FEB through source resolution, inspection, human-bound Analysis Intent approval, immutable model adoption, locked preflight, a new FEBio 4.12 nonlinear solve, official-FBS result validation, and deterministic reporting without reusing old solver outputs or placing real CAE data in Git.

**Architecture:** The installed `febio-cae` launcher is the only CAE command surface. The existing aligned FEB and its historical LOG/XPLT are external read-only evidence. Every new solver artifact is created under one new attempt in the existing `02_CAE` case, verified there, and promoted create-new only after the composite completion gates pass. The existing `CASE_MANIFEST.json` is the only overwrite-capable projection; all other pre-existing files are immutable.

**Tech Stack:** Released `febio-cae` CLI, Windows PowerShell 5.1, FEBio 4.12.0, locked official FBS CPython 3.13 runtime, JSON Schema, SHA-256

## Dependencies and stop conditions

- Complete Phase 1A through Phase 1D and install a verified release before starting this plan.
- Read and obey the root, `02_CAE`, and nearest case `AGENTS.md`. Git and GitHub are prohibited below `02_CAE`.
- Run only `%LOCALAPPDATA%\FEBioCaeHarness\bin\febio-cae.cmd`; do not use a source-tree virtual environment, direct `febio4.exe`, direct FBS Python, Gmsh, or FEBio Studio.
- Do not treat the current approval of the harness design or this implementation plan as Analysis Intent approval. Task 4 deliberately stops for a fresh, nonce-bound human response.
- Do not use the historical LOG/XPLT as the new attempt result. They are comparison/provenance evidence only.
- If any fixed path, byte count, SHA-256, executable identity, runtime-tree identity, model invariant, approval binding, or pre-existing file hash differs, stop before the next mutating step.
- A failed or incomplete solve may be diagnosed, but this acceptance contract contains no preapproved numerical change. Do not retry with a modified FEB without a new Analysis Intent revision and human approval.
- No Task in this plan commits anything. Real-model artifacts remain in `02_CAE`; the tool repository must be clean before and after the run.

## Fixed identities

| Role | Path | Bytes | SHA-256 |
|---|---|---:|---|
| Authoritative aligned FEB | `C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\02_Bottom_Frame_v1.0,0729_CAE_Gmsh_Tet10_2p0mm_local040_cylD2_L4.feb` | 11284164 | `6351FC75C98BA4830AFC020B1A381A7755775F2BD4C65823477DD9E216070049` |
| Prohibited predecessor FEB | `C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_2p0mm\02_Bottom_Frame_v1.0,0729_CAE_Gmsh_Tet10_2p0mm_local040_cylD2_L4.feb` | 11292359 | `CFAA4F106108DA1D2708D7D6EFE01390FA8E882DA5DF99F0B22028A63631A28F` |
| Historical LOG | `C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\normal-solve.log` | 76511 | `1FC4755DD8D82BEF322D752B57D588FFA0B930512938CF1B585F9EA59A55481B` |
| Historical XPLT | `C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\normal-solve.xplt` | 57600908 | `BD9444F72231B8591D9A1C17AFAD41C384B2767EAA778B1EA19933E40ABEFC7C` |
| Build report | `C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\build-report.json` | 2980 | `B0BE9C4DB6032B2783C23BBB102CAA8DC9D6CFAC9C9FD86C2E3B7A8CF4112A9C` |
| Provenance | `C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\provenance.json` | 775 | `D1C9D46B3A662C92C1BD3ACFA30FAFE948E31C28C860557DCB31A511BC0ED85A` |
| Final validation | `C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\final-validation.json` | 53992 | `1A66AED65C485F3CB6CFE400FB327E7D28D17E0FA8726593094FB3F7B032DF60` |
| Assembly STEP | `C:\Users\backo\Downloads\V2.3_Assy,CAE_validation.step` | 890105 | `38EA190F17ABAED8061CF2EB065C5611DA907DBB2EF98AE3978FDCF10C70FED6` |

Additional fixed identities:

```text
case directory          = C:\Users\backo\OneDrive\Documents\FEBio\02_CAE\01_Active\Bottom_Frame\2026-07-30_0729C_local040-screw
analysis_id             = 2026-07-30_0729C_local040-screw
lineage branch          = codex/local040-rigid-screw-cylinder
lineage commit          = 3c0e672313962e4d14b4d541d27ae8245488b75d
ABS/domain signature    = 4859FBEAC85C365F76023B3DB1D47D367185B49502E70B697ED1664783002A40
FEBio executable        = C:\Program Files\FEBioStudio\bin\febio4.exe
FEBio executable hash   = 03B9DB12C4B3E2ED0CF027BE6B8B5D0CEF2D4EDC9EAB26F5A2BD8193EFB770C9
required LOG version    = 4.12.0
FBS runtime tree hash   = 1CD91A25E4E4E4B9454E2AAFFFBB9736B63534B9CA3D2807A0250EC77C869BD0
```

## Common PowerShell invocation rule

`shell_command` invocations do not share PowerShell variables. Prepend the
following preamble once and concatenate **all PowerShell fences in one Task,
in Step order, into one `powershell.exe` invocation**. A numbered Step is a
review checkpoint, not a new shell lifetime. Task 4 deliberately ends its
first invocation after Step 5 for human approval; its Step 6 explicitly
re-establishes variables and reloads the outstanding immutable request.
Never repeat a mutating CLI command merely to reconstruct an in-memory
variable; reload authoritative evidence with `case status`, `intent show`, or
the recorded case artifact instead.

```powershell
$ErrorActionPreference = 'Stop'
$ToolRoot = 'C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\febio-tools'
$CaseDir = 'C:\Users\backo\OneDrive\Documents\FEBio\02_CAE\01_Active\Bottom_Frame\2026-07-30_0729C_local040-screw'
$CaeRoot = 'C:\Users\backo\OneDrive\Documents\FEBio\02_CAE'
$Cli = Join-Path $env:LOCALAPPDATA 'FEBioCaeHarness\bin\febio-cae.cmd'
$SourceFeb = 'C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\02_Bottom_Frame_v1.0,0729_CAE_Gmsh_Tet10_2p0mm_local040_cylD2_L4.feb'
$RejectedFeb = 'C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_2p0mm\02_Bottom_Frame_v1.0,0729_CAE_Gmsh_Tet10_2p0mm_local040_cylD2_L4.feb'
$env:PYTHONPATH = $null
$env:PYTHONHOME = $null
$env:PIP_EDITABLE = $null

function Get-EvidenceRecord {
    param(
        [Parameter(Mandatory=$true)]$Response,
        [Parameter(Mandatory=$true)][string]$Kind
    )
    $Matches = @($Response.evidence | Where-Object { $_.kind -eq $Kind })
    if ($Matches.Count -ne 1) {
        throw "EVIDENCE_KIND_COUNT: kind=$Kind count=$($Matches.Count)"
    }
    return $Matches[0].data
}

function Get-ArtifactRecord {
    param(
        [Parameter(Mandatory=$true)]$Response,
        [Parameter(Mandatory=$true)][string]$Role
    )
    $Matches = @($Response.artifacts | Where-Object { $_.role -eq $Role })
    if ($Matches.Count -ne 1) {
        throw "ARTIFACT_ROLE_COUNT: role=$Role count=$($Matches.Count)"
    }
    return $Matches[0]
}
```

---

## Task 1: Read-only installed-release and workspace preflight

**Files:**

- Read: root, `02_CAE`, and case `AGENTS.md`
- Read: installed release manifest and installed launcher
- Read: every pre-existing case file
- Create outside the case: one canonical inventory JSON under the OS temporary
  directory
- Modify: none

**Interfaces:**

- Consumes: Phase 1D installed-release `--version` JSON contract and repository-boundary policy.
- Produces: `$BeforeCaseInventory`, a canonical pre-existing inventory JSON in
  an external OS temporary directory, `$BeforeWorktreeInventory`, and
  `$InstalledIdentity`. Task 2 copies the verified inventory create-new into
  case evidence before the human-approval pause.

- [ ] **Step 1: Establish exact paths and remove source-tree influence**

```powershell
$ErrorActionPreference = 'Stop'
$ToolRoot = 'C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\febio-tools'
$CaseDir = 'C:\Users\backo\OneDrive\Documents\FEBio\02_CAE\01_Active\Bottom_Frame\2026-07-30_0729C_local040-screw'
$CaeRoot = 'C:\Users\backo\OneDrive\Documents\FEBio\02_CAE'
$Cli = Join-Path $env:LOCALAPPDATA 'FEBioCaeHarness\bin\febio-cae.cmd'
$SourceFeb = 'C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\02_Bottom_Frame_v1.0,0729_CAE_Gmsh_Tet10_2p0mm_local040_cylD2_L4.feb'
$RejectedFeb = 'C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_2p0mm\02_Bottom_Frame_v1.0,0729_CAE_Gmsh_Tet10_2p0mm_local040_cylD2_L4.feb'
$env:PYTHONPATH = $null
$env:PYTHONHOME = $null
$env:PIP_EDITABLE = $null

function Get-EvidenceRecord {
    param(
        [Parameter(Mandatory=$true)]$Response,
        [Parameter(Mandatory=$true)][string]$Kind
    )
    $Matches = @($Response.evidence | Where-Object { $_.kind -eq $Kind })
    if ($Matches.Count -ne 1) {
        throw "EVIDENCE_KIND_COUNT: kind=$Kind count=$($Matches.Count)"
    }
    return $Matches[0].data
}

function Get-ArtifactRecord {
    param(
        [Parameter(Mandatory=$true)]$Response,
        [Parameter(Mandatory=$true)][string]$Role
    )
    $Matches = @($Response.artifacts | Where-Object { $_.role -eq $Role })
    if ($Matches.Count -ne 1) {
        throw "ARTIFACT_ROLE_COUNT: role=$Role count=$($Matches.Count)"
    }
    return $Matches[0]
}

if (-not (Test-Path -LiteralPath $Cli -PathType Leaf)) {
    throw 'INSTALLED_CLI_MISSING'
}
if (-not (Test-Path -LiteralPath $CaseDir -PathType Container)) {
    throw 'BOTTOMFRAME_CASE_MISSING'
}
```

- [ ] **Step 2: Verify governing rules and the no-Git boundary**

```powershell
Get-Content -LiteralPath (Join-Path (Split-Path $CaeRoot -Parent) 'AGENTS.md') -Encoding UTF8
Get-Content -LiteralPath (Join-Path $CaeRoot 'AGENTS.md') -Encoding UTF8

$GitMarkers = Get-ChildItem -LiteralPath $CaeRoot -Recurse -Force |
    Where-Object { $_.Name -eq '.git' }
if ($GitMarkers) {
    throw "GIT_MARKER_IN_CAE: $($GitMarkers.FullName -join ', ')"
}
```

Expected: no Git command is invoked against the CAE tree and no `.git` marker
exists below `02_CAE`.

- [ ] **Step 3: Capture the immutable pre-run case inventory in memory**

```powershell
$BeforeCaseInventory = @(
    Get-ChildItem -LiteralPath $CaseDir -Recurse -Force -File |
        Sort-Object FullName |
        ForEach-Object {
            [pscustomobject]@{
                relative_path = $_.FullName.Substring($CaseDir.Length + 1).Replace('\','/')
                bytes = $_.Length
                sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
            }
        }
)
if (-not ($BeforeCaseInventory.relative_path -contains 'CASE_MANIFEST.json')) {
    throw 'LEGACY_CASE_MANIFEST_MISSING'
}
$InventoryPayload = [ordered]@{
    schema_version = 1
    files = @($BeforeCaseInventory)
}
$InventoryJson = $InventoryPayload | ConvertTo-Json -Depth 8 -Compress
$InventoryTempRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
    'FEBioCaeHarness\phase1e-20260730-bottomframe'
if (-not (Test-Path -LiteralPath $InventoryTempRoot -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $InventoryTempRoot)
}
$BeforeInventoryPath = Join-Path $InventoryTempRoot 'preexisting-inventory.json'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$InventoryStream = [System.IO.File]::Open(
    $BeforeInventoryPath,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::Read
)
$InventoryWriter = [System.IO.StreamWriter]::new(
    $InventoryStream,
    $Utf8NoBom
)
try {
    $InventoryWriter.Write($InventoryJson + "`n")
    $InventoryWriter.Flush()
    $InventoryStream.Flush($true)
}
finally {
    $InventoryWriter.Dispose()
    $InventoryStream.Dispose()
}
$BeforeInventorySha256 = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $BeforeInventoryPath
).Hash
```

- [ ] **Step 4: Verify the released CLI is installed and content-bound**

```powershell
$VersionStdout = & $Cli --version
if ($LASTEXITCODE -ne 0) {
    throw "INSTALLED_CLI_VERSION_FAILED: exit=$LASTEXITCODE"
}
$VersionResult = $VersionStdout | ConvertFrom-Json
if ($VersionResult.status -ne 'success') {
    throw 'INSTALLED_CLI_VERSION_NOT_SUCCESS'
}
$InstalledIdentity = Get-EvidenceRecord $VersionResult 'installed-identity'
if ($InstalledIdentity.package_path -like "$ToolRoot*") {
    throw 'SOURCE_TREE_INSTALL_PROHIBITED'
}
if (-not $InstalledIdentity.wheel.sha256) {
    throw 'INSTALLED_WHEEL_HASH_MISSING'
}
if (-not $InstalledIdentity.build_provenance.sha256) {
    throw 'BUILD_PROVENANCE_HASH_MISSING'
}
foreach ($Pointer in @(
    $InstalledIdentity.release.install_manifest,
    $InstalledIdentity.release.release_manifest,
    $InstalledIdentity.release.repository_policy
) + @($InstalledIdentity.release.archives)) {
    if (-not (Test-Path -LiteralPath $Pointer.path -PathType Leaf) -or
        (Get-Item -LiteralPath $Pointer.path).Length -ne $Pointer.bytes -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $Pointer.path).Hash -ne
        $Pointer.sha256) {
        throw "INSTALLED_RELEASE_POINTER_DRIFT: $($Pointer.path)"
    }
}
$ArchiveRoles = @($InstalledIdentity.release.archives.role)
if (Compare-Object @(
    'harness-wheel',
    'harness-sdist',
    'codex-skill-archive'
) $ArchiveRoles) {
    throw 'INSTALLED_RELEASE_ARCHIVE_ROLE_MISMATCH'
}
$PackageRoot = [System.IO.Path]::GetFullPath(
    [string]$InstalledIdentity.package_path
)
$InstalledVenv = Split-Path (
    Split-Path (
        Split-Path $PackageRoot -Parent
    ) -Parent
) -Parent
$InstalledPython = Join-Path $InstalledVenv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $InstalledPython -PathType Leaf)) {
    throw 'INSTALLED_RELEASE_PYTHON_MISSING'
}
if ($InstalledIdentity.fbs_runtime.sha256 -ne
    '1CD91A25E4E4E4B9454E2AAFFFBB9736B63534B9CA3D2807A0250EC77C869BD0') {
    throw 'FBS_RUNTIME_PROFILE_DRIFT'
}
```

- [ ] **Step 5: Verify the tool-repository baseline**

```powershell
$BeforeWorktreeLines = & git -C $ToolRoot worktree list --porcelain
$BeforeWorktreePaths = @(
    $BeforeWorktreeLines |
        Where-Object { $_ -like 'worktree *' } |
        ForEach-Object { $_.Substring(9) }
)
$BeforeWorktreeInventory = @(
    foreach ($WorktreePath in $BeforeWorktreePaths) {
        $CanonicalWorktreePath = [System.IO.Path]::GetFullPath(
            $WorktreePath
        )
        $CanonicalCaeRoot = [System.IO.Path]::GetFullPath(
            $CaeRoot
        ).TrimEnd('\')
        if (
            $CanonicalWorktreePath -ieq $CanonicalCaeRoot -or
            $CanonicalWorktreePath.StartsWith(
                $CanonicalCaeRoot + '\',
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "TOOL_WORKTREE_INSIDE_CAE_TREE: $CanonicalWorktreePath"
        }
        [pscustomobject]@{
            path = $CanonicalWorktreePath
            status = @(& git -C $CanonicalWorktreePath status --porcelain=v1 --untracked-files=all)
            ignored = @(& git -C $CanonicalWorktreePath ls-files --others --ignored --exclude-standard)
        }
    }
)
$WorktreeInventoryPath = Join-Path $InventoryTempRoot `
    'preexisting-worktree-inventory.json'
$WorktreeInventoryJson = [ordered]@{
    schema_version = 1
    worktrees = @($BeforeWorktreeInventory)
} | ConvertTo-Json -Depth 8 -Compress
$WorktreeStream = [System.IO.File]::Open(
    $WorktreeInventoryPath,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::Read
)
$WorktreeWriter = [System.IO.StreamWriter]::new(
    $WorktreeStream,
    $Utf8NoBom
)
try {
    $WorktreeWriter.Write($WorktreeInventoryJson + "`n")
    $WorktreeWriter.Flush()
    $WorktreeStream.Flush($true)
}
finally {
    $WorktreeWriter.Dispose()
    $WorktreeStream.Dispose()
}
$ToolStatus = & git -C $ToolRoot status --short
if ($ToolStatus) {
    throw "TOOL_REPOSITORY_NOT_CLEAN: $($ToolStatus -join ', ')"
}
```

Expected: no file has been written to the case and the tool repository is clean.

## Task 2: Adopt the legacy case and resolve the exact authoritative source

**Files:**

- Modify atomically: `CASE_MANIFEST.json`
- Create: `05_Verification/harness/legacy-manifests/*`
- Create: append-only harness event and source/input evidence under the case
- Create temporary request: `90_Temporary/harness-e2e/source-expectation.json`

**Interfaces:**

- Consumes: `febio-cae case adopt`, `source resolve`, `input ingest`, and the fixed identities above.
- Produces: an adopted `CASE_CREATED` case, one authoritative FEB `InputRecord`, lineage evidence records, and no model/attempt/process.

- [ ] **Step 1: Invoke legacy adoption with the in-memory baseline**

```powershell
$BeforeInventoryPath = Join-Path ([System.IO.Path]::GetTempPath()) `
    'FEBioCaeHarness\phase1e-20260730-bottomframe\preexisting-inventory.json'
if (-not (Test-Path -LiteralPath $BeforeInventoryPath -PathType Leaf)) {
    throw 'EXTERNAL_PREEXISTING_INVENTORY_MISSING'
}
$BeforeInventorySha256 = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $BeforeInventoryPath
).Hash
$BeforeInventoryPayload = Get-Content -LiteralPath $BeforeInventoryPath `
    -Encoding UTF8 -Raw | ConvertFrom-Json
$BeforeCaseInventory = @($BeforeInventoryPayload.files)
$LegacyManifestRow = @(
    $BeforeCaseInventory |
        Where-Object { $_.relative_path -eq 'CASE_MANIFEST.json' }
)
if ($LegacyManifestRow.Count -ne 1) {
    throw "LEGACY_MANIFEST_ROW_COUNT: $($LegacyManifestRow.Count)"
}
$AdoptStdout = & $Cli case adopt `
    --case-dir $CaseDir `
    --analysis-id '2026-07-30_0729C_local040-screw' `
    --expected-legacy-manifest-sha256 $LegacyManifestRow[0].sha256 `
    --preexisting-inventory-json $BeforeInventoryPath `
    --expected-preexisting-inventory-sha256 $BeforeInventorySha256
if ($LASTEXITCODE -ne 0) {
    throw "CASE_ADOPT_FAILED: exit=$LASTEXITCODE"
}
$Adopt = $AdoptStdout | ConvertFrom-Json
$AdoptionEvidence = Get-EvidenceRecord $Adopt 'case-adoption'
if ($Adopt.case_state -ne 'CASE_CREATED') {
    throw "UNEXPECTED_ADOPT_STATE: $($Adopt.case_state)"
}
if ($AdoptionEvidence.preexisting_inventory_artifact_sha256 -ne
    $BeforeInventorySha256) {
    throw 'PREEXISTING_INVENTORY_PERSISTENCE_MISMATCH'
}
$PersistedInventoryPath = [string]$AdoptionEvidence.preexisting_inventory_artifact_path
if (-not [System.IO.Path]::IsPathRooted($PersistedInventoryPath)) {
    $PersistedInventoryPath = Join-Path $CaseDir $PersistedInventoryPath
}
if (-not (Test-Path -LiteralPath $PersistedInventoryPath -PathType Leaf)) {
    throw 'PERSISTED_PREEXISTING_INVENTORY_MISSING'
}
$RequestDir = Join-Path $CaseDir '90_Temporary\harness-e2e'
if (Test-Path -LiteralPath $RequestDir) {
    throw 'E2E_REQUEST_DIRECTORY_ALREADY_EXISTS'
}
[void](New-Item -ItemType Directory -Path $RequestDir)
```

- [ ] **Step 2: Create the exact source-expectation request**

Use `apply_patch` to create this new file under
`90_Temporary/harness-e2e/source-expectation.json`:

Before applying the patch, require
`Test-Path -LiteralPath $SourceRequest` to be false. `apply_patch` must add,
never update, this request file.

```powershell
$SourceRequest = Join-Path $CaseDir `
    '90_Temporary\harness-e2e\source-expectation.json'
if (Test-Path -LiteralPath $SourceRequest) {
    throw 'SOURCE_EXPECTATION_REQUEST_ALREADY_EXISTS'
}
```

```json
{
  "schema_version": 1,
  "role": "authoritative-feb",
  "preferred_path": "C:\\dev\\FEBio\\jobs\\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\\02_Bottom_Frame_v1.0,0729_CAE_Gmsh_Tet10_2p0mm_local040_cylD2_L4.feb",
  "expected_sha256": "6351FC75C98BA4830AFC020B1A381A7755775F2BD4C65823477DD9E216070049",
  "expected_bytes": 11284164,
  "search_roots": [
    "C:\\dev\\FEBio\\jobs\\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm"
  ],
  "prohibited_candidates": [
    {
      "sha256": "CFAA4F106108DA1D2708D7D6EFE01390FA8E882DA5DF99F0B22028A63631A28F",
      "reason": "superseded unaligned predecessor",
      "canonical_path": "C:\\dev\\FEBio\\jobs\\0729C_CAE_local040_cylD2_L4_2p0mm\\02_Bottom_Frame_v1.0,0729_CAE_Gmsh_Tet10_2p0mm_local040_cylD2_L4.feb",
      "bytes": 11292359
    }
  ],
  "lineage_evidence": [
    {
      "kind": "build-report",
      "canonical_path": "C:\\dev\\FEBio\\jobs\\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\\build-report.json",
      "sha256": "B0BE9C4DB6032B2783C23BBB102CAA8DC9D6CFAC9C9FD86C2E3B7A8CF4112A9C",
      "bytes": 2980
    },
    {
      "kind": "provenance",
      "canonical_path": "C:\\dev\\FEBio\\jobs\\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\\provenance.json",
      "sha256": "D1C9D46B3A662C92C1BD3ACFA30FAFE948E31C28C860557DCB31A511BC0ED85A",
      "bytes": 775
    },
    {
      "kind": "final-validation",
      "canonical_path": "C:\\dev\\FEBio\\jobs\\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\\final-validation.json",
      "sha256": "1A66AED65C485F3CB6CFE400FB327E7D28D17E0FA8726593094FB3F7B032DF60",
      "bytes": 53992
    },
    {
      "kind": "assembly-step",
      "canonical_path": "C:\\Users\\backo\\Downloads\\V2.3_Assy,CAE_validation.step",
      "sha256": "38EA190F17ABAED8061CF2EB065C5611DA907DBB2EF98AE3978FDCF10C70FED6",
      "bytes": 890105
    }
  ],
  "reference_results": [
    {
      "kind": "historical-log",
      "canonical_path": "C:\\dev\\FEBio\\jobs\\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\\normal-solve.log",
      "sha256": "1FC4755DD8D82BEF322D752B57D588FFA0B930512938CF1B585F9EA59A55481B",
      "bytes": 76511,
      "reuse": false
    },
    {
      "kind": "historical-xplt",
      "canonical_path": "C:\\dev\\FEBio\\jobs\\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\\normal-solve.xplt",
      "sha256": "BD9444F72231B8591D9A1C17AFAD41C384B2767EAA778B1EA19933E40ABEFC7C",
      "bytes": 57600908,
      "reuse": false
    }
  ],
  "lineage": {
    "branch": "codex/local040-rigid-screw-cylinder",
    "commit": "3c0e672313962e4d14b4d541d27ae8245488b75d"
  }
}
```

- [ ] **Step 3: Resolve and ingest without copying historical result bytes**

```powershell
$SourceRequest = Join-Path $CaseDir '90_Temporary\harness-e2e\source-expectation.json'
$ResolveStdout = & $Cli source resolve --case-dir $CaseDir --request-json $SourceRequest
if ($LASTEXITCODE -ne 0) {
    throw "SOURCE_RESOLVE_FAILED: exit=$LASTEXITCODE"
}
$Resolve = $ResolveStdout | ConvertFrom-Json
$SourceResolution = Get-EvidenceRecord $Resolve 'source-resolution'
if ($SourceResolution.selected.sha256 -ne
    '6351FC75C98BA4830AFC020B1A381A7755775F2BD4C65823477DD9E216070049') {
    throw 'AUTHORITATIVE_SOURCE_HASH_MISMATCH'
}
if ($SourceResolution.selected.path -eq $RejectedFeb) {
    throw 'PROHIBITED_PREDECESSOR_SELECTED'
}
if ($SourceResolution.source_selection_approval_required) {
    throw 'UNEXPECTED_ALTERNATE_SOURCE_APPROVAL'
}

$IngestStdout = & $Cli input ingest --case-dir $CaseDir --role authoritative-feb
if ($LASTEXITCODE -ne 0) {
    throw "INPUT_INGEST_FAILED: exit=$LASTEXITCODE"
}
$Ingest = $IngestStdout | ConvertFrom-Json
$InputRecord = Get-EvidenceRecord $Ingest 'input-record'
if ($InputRecord.destination_role -ne 'authoritative-feb' -or
    $InputRecord.canonical_path -ne $SourceFeb -or
    $InputRecord.bytes -ne 11284164 -or
    $InputRecord.sha256 -ne
    '6351FC75C98BA4830AFC020B1A381A7755775F2BD4C65823477DD9E216070049') {
    throw 'AUTHORITATIVE_INPUT_RECORD_MISMATCH'
}
```

- [ ] **Step 4: Assert the no-side-effect source gate**

```powershell
$PrematureArtifacts = @(
    Get-ChildItem -LiteralPath (Join-Path $CaseDir '02_Model') -Force
    Get-ChildItem -LiteralPath (Join-Path $CaseDir '03_Result') -Force
    Get-ChildItem -LiteralPath (Join-Path $CaseDir '04_Report') -Force
)
if ($PrematureArtifacts) {
    throw 'PREMATURE_MODEL_RESULT_OR_REPORT'
}
$AttemptDirs = Get-ChildItem -LiteralPath (Join-Path $CaseDir '90_Temporary') `
    -Recurse -Directory -Filter 'attempt-*'
if ($AttemptDirs) {
    throw 'ATTEMPT_CREATED_BEFORE_APPROVAL'
}
```

Expected: exact preferred hash selected, predecessor rejected, state still `CASE_CREATED`, and no model, attempt, or solver process exists.

## Task 3: Inspect the external FEB and prove the reviewed model identity

**Files:**

- Read: exact external authoritative FEB
- Create: inspection and inheritance evidence under `05_Verification/harness`
- Modify atomically: `CASE_MANIFEST.json`

**Interfaces:**

- Consumes: `febio-cae inspect feb|inheritance`.
- Produces: schema-validated inspection evidence and `INPUT_INSPECTED`.

- [ ] **Step 1: Run the external FEB inspection**

```powershell
$InspectStdout = & $Cli inspect feb --case-dir $CaseDir `
    --exclude-domain 'M2_Screw_Indenter'
if ($LASTEXITCODE -ne 0) {
    throw "FEB_INSPECTION_FAILED: exit=$LASTEXITCODE"
}
$Inspect = $InspectStdout | ConvertFrom-Json
if ($Inspect.case_state -ne 'INPUT_INSPECTED') {
    throw "UNEXPECTED_INSPECTION_STATE: $($Inspect.case_state)"
}
```

- [ ] **Step 2: Assert the exact structural and physical setup**

```powershell
$I = Get-EvidenceRecord $Inspect 'feb-inspection'
if ($I.spec_version -ne '4.0' -or
    $I.source_sha256 -ne
    '6351FC75C98BA4830AFC020B1A381A7755775F2BD4C65823477DD9E216070049' -or
    $I.source_bytes -ne 11284164) {
    throw 'FEB_IDENTITY_OR_SCHEMA_MISMATCH'
}
$AbsMaterial = @(
    $I.materials |
        Where-Object { $_.name -eq 'ABS_Terluran_GP22_23C_QS_N-mm' }
)
if ($AbsMaterial.Count -ne 1 -or
    $AbsMaterial[0].type -ne 'reactive plasticity') {
    throw 'ABS_MATERIAL_MISMATCH'
}
$RigidMaterial = @(
    $I.materials |
        Where-Object { $_.name -eq 'M2_Screw_Rigid' }
)
if ($RigidMaterial.Count -ne 1 -or
    $RigidMaterial[0].type -ne 'rigid body') {
    throw 'RIGID_MATERIAL_MISMATCH'
}
$Part2 = @($I.domains | Where-Object { $_.name -eq 'Part2' })
if ($Part2.Count -ne 1 -or
    $Part2[0].element_type -ne 'tet10' -or
    $Part2[0].element_count -ne 47289 -or
    $Part2[0].referenced_node_count -ne 82066) {
    throw 'PART2_DOMAIN_MISMATCH'
}
$Screw = @(
    $I.domains | Where-Object { $_.name -eq 'M2_Screw_Indenter' }
)
if ($Screw.Count -ne 1 -or
    $Screw[0].element_type -ne 'tet4' -or
    $Screw[0].element_count -ne 2642 -or
    $Screw[0].referenced_node_count -ne 713) {
    throw 'SCREW_DOMAIN_MISMATCH'
}
if ($I.domain_signature_version -ne 'feb-domain-signature-v1' -or
    $I.domain_signature -ne
    '4859FBEAC85C365F76023B3DB1D47D367185B49502E70B697ED1664783002A40') {
    throw 'DOMAIN_SIGNATURE_MISMATCH'
}
if (@($I.references | Where-Object { -not $_.resolved }).Count -ne 0) {
    throw 'UNRESOLVED_FEB_REFERENCE'
}
function Get-PhysicsItem {
    param([string]$Section, [string]$Name)
    $Found = @(
        $I.physics_items |
            Where-Object { $_.section -eq $Section -and $_.name -eq $Name }
    )
    if ($Found.Count -ne 1) {
        throw "PHYSICS_ITEM_COUNT: section=$Section name=$Name count=$($Found.Count)"
    }
    return $Found[0]
}
function Get-PhysicsChildText {
    param($Item, [string]$Tag)
    $Found = @($Item.children | Where-Object { $_.tag -eq $Tag })
    if ($Found.Count -ne 1) {
        throw "PHYSICS_CHILD_COUNT: item=$($Item.name) tag=$Tag count=$($Found.Count)"
    }
    return [string]$Found[0].text
}
$Pair = Get-PhysicsItem 'SurfacePair' 'M2_Screw_Frictionless'
$Contact = Get-PhysicsItem 'Contact' 'M2_Screw_Frictionless'
if ((Get-PhysicsChildText $Pair 'primary') -ne 'NormalDisplacement2' -or
    (Get-PhysicsChildText $Pair 'secondary') -ne 'M2_Screw_Contact' -or
    $Contact.attributes.surface_pair -ne 'M2_Screw_Frictionless' -or
    $Contact.type -ne 'sliding-elastic' -or
    [double](Get-PhysicsChildText $Contact 'fric_coeff') -ne 0.0) {
    throw 'CONTACT_SETUP_MISMATCH'
}
$BottomFix = Get-PhysicsItem 'Boundary' 'BottomFix'
$Mirror = Get-PhysicsItem 'Boundary' 'Mirror'
if ($BottomFix.attributes.node_set -ne '@surface:ZeroDisplacement1' -or
    $Mirror.attributes.node_set -ne '@surface:ZeroDisplacement3') {
    throw 'BOUNDARY_SETUP_MISMATCH'
}
```

- [ ] **Step 3: Assert rigid kinematics, control, and requested output availability**

```powershell
$ExpectedVector = @(
    -0.7245040154804102,
     0.12365039951009645,
     1.8600549750622306
)
$RigidNames = @(
    'PushByScrew_Rigid_X',
    'PushByScrew_Rigid_Y',
    'PushByScrew_Rigid_Z'
)
$RigidDofs = @('x', 'y', 'z')
for ($k = 0; $k -lt 3; $k++) {
    $RigidItem = Get-PhysicsItem 'Rigid' $RigidNames[$k]
    $ValueChild = @(
        $RigidItem.children | Where-Object { $_.tag -eq 'value' }
    )
    if ($RigidItem.type -ne 'rigid_displacement' -or
        (Get-PhysicsChildText $RigidItem 'rb') -ne 'M2_Screw_Rigid' -or
        (Get-PhysicsChildText $RigidItem 'dof') -ne $RigidDofs[$k] -or
        $ValueChild.Count -ne 1 -or
        $ValueChild[0].attributes.lc -ne '1' -or
        [math]::Abs(
            [double](Get-PhysicsChildText $RigidItem 'value') -
            $ExpectedVector[$k]
        ) -gt 1e-12) {
        throw "RIGID_VECTOR_MISMATCH_COMPONENT_$k"
    }
}
$VectorMagnitude = [math]::Sqrt(
    ($ExpectedVector | ForEach-Object { $_ * $_ } | Measure-Object -Sum).Sum
)
if ([math]::Abs($VectorMagnitude - 2.0) -gt 1e-12) {
    throw 'RIGID_MAGNITUDE_MISMATCH'
}
$FixedRotations = Get-PhysicsItem 'Rigid' 'M2_Screw_FixRotations'
if ((Get-PhysicsChildText $FixedRotations 'Ru_dof') -ne '1' -or
    (Get-PhysicsChildText $FixedRotations 'Rv_dof') -ne '1' -or
    (Get-PhysicsChildText $FixedRotations 'Rw_dof') -ne '1') {
    throw 'RIGID_ROTATION_CONSTRAINT_MISMATCH'
}
if (@($I.controls).Count -ne 1) { throw 'CONTROL_COUNT_MISMATCH' }
$Control = $I.controls[0]
if ([int]$Control.time_steps -ne 20 -or
    [math]::Abs([double]$Control.step_size - 0.05) -gt 1e-15 -or
    $Control.PSObject.Properties.Name -contains 'time_stepper') {
    throw 'FIXED_STEP_CONTROL_MISMATCH'
}
$LoadController = Get-PhysicsItem 'LoadData' 'PushByScrew_Ramp'
if ($LoadController.type -ne 'loadcurve' -or
    $LoadController.attributes.id -ne '1' -or
    (Get-PhysicsChildText $LoadController 'interpolate') -ne 'LINEAR') {
    throw 'LOAD_CONTROLLER_MISMATCH'
}
$ExpectedPlot = @('displacement','stress','relative volume')
if (Compare-Object $ExpectedPlot @($I.output_fields)) {
    throw 'PLOT_VARIABLE_MISMATCH'
}
```

- [ ] **Step 4: Verify lineage evidence without trusting it as new results**

```powershell
$InheritanceStdout = & $Cli inspect inheritance --case-dir $CaseDir
if ($LASTEXITCODE -ne 0) {
    throw "INHERITANCE_INSPECTION_FAILED: exit=$LASTEXITCODE"
}
$Inheritance = $InheritanceStdout | ConvertFrom-Json
$InheritanceEvidence = Get-EvidenceRecord $Inheritance 'inheritance-inspection'
if ($InheritanceEvidence.reference_results_reusable_as_attempt_output) {
    throw 'REFERENCE_RESULT_REUSE_ENABLED'
}
$SourceRequest = Join-Path $CaseDir `
    '90_Temporary\harness-e2e\source-expectation.json'
$SourceExpectation = Get-Content -LiteralPath $SourceRequest -Encoding UTF8 -Raw |
    ConvertFrom-Json
$ExpectedPointers = @(
    @($SourceExpectation.lineage_evidence) +
    @($SourceExpectation.reference_results)
)
$EmittedPointers = @(
    @($InheritanceEvidence.lineage_evidence) +
    @($InheritanceEvidence.reference_results)
)
if ($EmittedPointers.Count -ne $ExpectedPointers.Count) {
    throw 'INHERITANCE_POINTER_COUNT_MISMATCH'
}
foreach ($Expected in $ExpectedPointers) {
    $Matches = @($EmittedPointers | Where-Object {
        $_.kind -eq $Expected.kind -and
        $_.canonical_path -eq $Expected.canonical_path -and
        $_.bytes -eq $Expected.bytes -and
        $_.sha256 -eq $Expected.sha256
    })
    if ($Matches.Count -ne 1) {
        throw "INHERITANCE_POINTER_BINDING_MISMATCH: $($Expected.kind)"
    }
    $Pointer = $Matches[0]
    if (-not (Test-Path -LiteralPath $Pointer.canonical_path -PathType Leaf) -or
        (Get-Item -LiteralPath $Pointer.canonical_path).Length -ne $Pointer.bytes -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $Pointer.canonical_path).Hash -ne
        $Pointer.sha256) {
        throw "LINEAGE_POINTER_DRIFT: $($Pointer.kind)"
    }
}
foreach ($Reference in @($InheritanceEvidence.reference_results)) {
    if ($Reference.reuse -ne $false) {
        throw "REFERENCE_RESULT_REUSE_FLAG_INVALID: $($Reference.kind)"
    }
}
```

Expected: all exact invariants pass, state is `INPUT_INSPECTED`, and no model/attempt/process exists.

## Task 4: Draft the exact Analysis Intent and stop for fresh human approval

**Files:**

- Create temporary request: `90_Temporary/harness-e2e/analysis-intent.json`
- Create: immutable intent revision and approval request under
  `05_Verification/harness/intent`
- Modify atomically: `CASE_MANIFEST.json`
- Create only after user response: one approval-input JSON under `90_Temporary/harness-e2e`

**Interfaces:**

- Consumes: `febio-cae intent draft|request-approval|approve|show`.
- Produces: `INTENT_DRAFTED`, a deliberate `waiting_for_human` command result
  that leaves the case at `INTENT_DRAFTED`, then `INTENT_APPROVED` only after
  an exact fresh human response.

- [ ] **Step 1: Create the complete intent request**

Use `apply_patch` to create
`90_Temporary/harness-e2e/analysis-intent.json` with this exact content:

Before applying the patch, require
`Test-Path -LiteralPath $IntentRequest` to be false. `apply_patch` must add,
never update, this request file.

```powershell
$IntentRequest = Join-Path $CaseDir `
    '90_Temporary\harness-e2e\analysis-intent.json'
if (Test-Path -LiteralPath $IntentRequest) {
    throw 'ANALYSIS_INTENT_REQUEST_ALREADY_EXISTS'
}
```

```json
{
  "schema_version": 1,
  "analysis_id": "2026-07-30_0729C_local040-screw",
  "engineering_question": "既存のaligned local040モデルについて、M2_Screw_Rigidを既定の2.0 mm線形ランプで押し込む20固定ステップ非線形解析が、モデルを変更せずFEBio 4.12で正常終了し、剛体変位・Part2有効応力・Part2相対体積を公式FBSで完全に読み出せるか。",
  "parts_and_roles": {
    "resolved": true,
    "value": {
      "Part2": "deformable ABS body",
      "M2_Screw_Indenter": "rigid M2 screw indenter"
    }
  },
  "unit_system": {
    "resolved": true,
    "value": "mm-N-s"
  },
  "materials": {
    "resolved": true,
    "value": {
      "Part2": {
        "name": "ABS_Terluran_GP22_23C_QS_N-mm",
        "type": "reactive plasticity"
      },
      "M2_Screw_Indenter": {
        "name": "M2_Screw_Rigid",
        "type": "rigid body"
      }
    }
  },
  "loads": {
    "resolved": true,
    "value": {
      "rigid_body": "M2_Screw_Rigid",
      "displacement_boundary_conditions": [
        "PushByScrew_Rigid_X",
        "PushByScrew_Rigid_Y",
        "PushByScrew_Rigid_Z"
      ],
      "final_vector_mm": [
        -0.7245040154804102,
        0.12365039951009645,
        1.8600549750622306
      ],
      "final_magnitude_mm": 2,
      "load_controller": "PushByScrew_Ramp"
    }
  },
  "constraints": {
    "resolved": true,
    "value": {
      "deformable": [
        {
          "name": "BottomFix",
          "surface": "ZeroDisplacement1"
        },
        {
          "name": "Mirror",
          "surface": "ZeroDisplacement3"
        }
      ],
      "rigid": [
        "M2_Screw_FixRotations"
      ]
    }
  },
  "contacts": {
    "resolved": true,
    "value": [
      {
        "name": "M2_Screw_Frictionless",
        "primary": "NormalDisplacement2",
        "secondary": "M2_Screw_Contact",
        "type": "sliding-elastic",
        "fric_coeff": 0
      }
    ]
  },
  "analysis_steps": {
    "resolved": true,
    "value": {
      "time_steps": 20,
      "step_size": 0.05,
      "end_time": 1,
      "adaptive_time_stepper": false
    }
  },
  "roi_and_protected_geometry": {
    "resolved": true,
    "value": {
      "roi": [
        "M2_Screw_Contact",
        "NormalDisplacement2",
        "Part2"
      ],
      "protected": [
        "Part2 mesh and topology",
        "M2_Screw_Indenter mesh and topology",
        "M2_Screw_Contact",
        "NormalDisplacement2"
      ]
    }
  },
  "result_requests": {
    "resolved": true,
    "value": [
      {
        "field": "displacement",
        "accessor": "nodeData",
        "components": [
          0,
          1,
          2,
          6
        ],
        "population": "M2_Screw_Indenter referenced node_index",
        "states": "all 21 FBS states"
      },
      {
        "field": "stress",
        "accessor": "elemData",
        "component": "fbs.post.MAT3DS.EFFECTIVE",
        "population": "Part2 ABS element_index",
        "states": "all 21 FBS states"
      },
      {
        "field": "relative volume",
        "accessor": "elemData",
        "component": 0,
        "population": "Part2 ABS element_index",
        "states": "all 21 FBS states"
      }
    ]
  },
  "load_path_and_physical_assumptions": {
    "resolved": true,
    "value": [
      "quasi-static nonlinear loading",
      "linear prescribed rigid-body displacement ramp",
      "frictionless screw contact",
      "rigid screw rotations fixed",
      "existing ABS constitutive calibration is inherited without validation"
    ]
  },
  "invariants": {
    "resolved": true,
    "value": [
      "authoritative FEB SHA-256 remains 6351FC75C98BA4830AFC020B1A381A7755775F2BD4C65823477DD9E216070049",
      "ABS/domain signature remains 4859FBEAC85C365F76023B3DB1D47D367185B49502E70B697ED1664783002A40",
      "mesh, material, load, boundary, contact, rigid, load-controller, output, and solver-control values remain byte-identical",
      "historical LOG and XPLT are evidence pointers only and are never reused as attempt outputs"
    ]
  },
  "allowed_numerical_changes": [],
  "mesh_and_result_comparison_criteria": {
    "resolved": true,
    "value": {
      "mesh": "exact adopted FEB bytes, domain signature, connectivity, populations, and binary32 initial coordinates",
      "result": "20 converged nonzero targets, one initial zero state, finite requested fields, and rigid-vector errors no greater than 1e-6 mm"
    }
  },
  "human_approval_required_for": [
    "this Analysis Intent revision before model adoption or solve",
    "every future change to the Analysis Intent",
    "every model, mesh, physics, or numerical-control modification"
  ],
  "unresolved_items": [],
  "prohibited_conclusions": [
    "ABS material calibration",
    "thread behavior",
    "friction behavior",
    "manufacturing robustness",
    "product strength",
    "design acceptance"
  ],
  "setting_provenance": [
    {
      "path": "/engineering_question",
      "state": "USER_SPECIFIED",
      "source": "approved Phase 1 BottomFrame E2E scope"
    },
    {
      "path": "/materials",
      "state": "INHERITED",
      "source": "exact authoritative FEB inspection"
    },
    {
      "path": "/loads",
      "state": "INHERITED",
      "source": "exact authoritative FEB inspection and verified build report"
    },
    {
      "path": "/allowed_numerical_changes",
      "state": "PROHIBITED",
      "source": "Phase 1 BottomFrame acceptance contract"
    }
  ],
  "expected_run": {
    "resolved": true,
    "value": {
      "solver_version": "4.12.0",
      "time_steps": 20,
      "end_time": 1,
      "required_fbs_state_count": 21,
      "automatic_retry_budget": 0,
      "execution_request_sha256": "CFBAACD6E66BE20BB6DEE9313D01A5D2FD3D5440B2DF53221A4CFE48F520D11D",
      "required_result_population": [
        "M2_Screw_Indenter displacement",
        "Part2 effective stress",
        "Part2 relative volume"
      ]
    }
  },
  "kinematic_checks": {
    "resolved": true,
    "value": [
      "all 713 rigid screw nodes at all 21 states follow time multiplied by the approved displacement vector within 1e-6 mm",
      "displacement component 6 is consistent with the vector norm within 1e-6 mm",
      "final displacement magnitude is 2.0 mm within 1e-6 mm"
    ]
  }
}
```

- [ ] **Step 2: Draft and inspect the immutable revision**

```powershell
$IntentRequest = Join-Path $CaseDir '90_Temporary\harness-e2e\analysis-intent.json'
$DraftStdout = & $Cli intent draft --case-dir $CaseDir --contract-json $IntentRequest
if ($LASTEXITCODE -ne 0) {
    throw "INTENT_DRAFT_FAILED: exit=$LASTEXITCODE"
}
$Draft = $DraftStdout | ConvertFrom-Json
$IntentRevision = Get-EvidenceRecord $Draft 'intent-revision'
if ($Draft.case_state -ne 'INTENT_DRAFTED' -or $IntentRevision.revision -ne 1) {
    throw 'INTENT_REVISION_MISMATCH'
}
if (-not $IntentRevision.contract_sha256 -or
    -not $IntentRevision.input_record_set_digest -or
    $null -ne $IntentRevision.source_selection_approval_record_digest) {
    throw 'INTENT_REVISION_BINDING_INCOMPLETE'
}
```

- [ ] **Step 3: Request approval and verify the deliberate stop**

```powershell
$ApprovalRequestStdout = & $Cli intent request-approval --case-dir $CaseDir
$ApprovalRequestExit = $LASTEXITCODE
$ApprovalRequest = $ApprovalRequestStdout | ConvertFrom-Json
if ($ApprovalRequestExit -ne 10 -or
    $ApprovalRequest.status -ne 'waiting_for_human' -or
    $ApprovalRequest.case_state -ne 'INTENT_DRAFTED') {
    throw 'APPROVAL_REQUEST_DID_NOT_STOP'
}
$ApprovalPacket = Get-EvidenceRecord $ApprovalRequest 'analysis-intent-approval-request'
if (-not $ApprovalPacket.nonce -or
    -not $ApprovalPacket.request_id -or
    -not $ApprovalPacket.contract_sha256 -or
    -not $ApprovalPacket.input_record_set_digest -or
    -not $ApprovalPacket.execution_profile_sha256) {
    throw 'APPROVAL_BINDING_INCOMPLETE'
}
if ($ApprovalPacket.source_selection_approval_record_digest -ne $null) {
    throw 'UNEXPECTED_SOURCE_SELECTION_APPROVAL_BINDING'
}
```

- [ ] **Step 4: Prove no forbidden side effect occurred while waiting**

```powershell
if (Get-ChildItem -LiteralPath (Join-Path $CaseDir '02_Model') -Force) {
    throw 'MODEL_CREATED_BEFORE_APPROVAL'
}
if (Get-ChildItem -LiteralPath (Join-Path $CaseDir '03_Result') -Force) {
    throw 'RESULT_CREATED_BEFORE_APPROVAL'
}
$LiveAttempts = Get-ChildItem -LiteralPath (Join-Path $CaseDir '90_Temporary') `
    -Recurse -Directory -Filter 'attempt-*'
if ($LiveAttempts) {
    throw 'ATTEMPT_CREATED_BEFORE_APPROVAL'
}
```

- [ ] **Step 5: Present the approval packet and pause**

Present to the user, in Japanese:

1. the engineering question;
2. all protected geometry and physical invariants;
3. the three requested FBS result populations;
4. `allowed_numerical_changes=[]` and
   `expected_run.value.automatic_retry_budget=0`, plus the exact
   `execution_request_sha256=CFBAACD6E66BE20BB6DEE9313D01A5D2FD3D5440B2DF53221A4CFE48F520D11D`
   that binds every timeout, resource limit, tolerance, state time, solver
   path/hash, allowance flag, and FBS runtime-tree hash;
5. the contract, input-record-set, source-selection-record (null when the exact preferred path was used), and execution-profile SHA-256 values;
6. revision and nonce;
7. the explicit limitations.

Ask for a fresh nonblank approval or rejection. Stop this execution turn. Do not
create approval prose on the user's behalf and do not infer approval from an
earlier `OK`.

- [ ] **Step 6: Record only the user's exact subsequent approval**

On the resumed turn, re-establish the fixed path variables and the
`Get-EvidenceRecord`/`Get-ArtifactRecord` helpers from Task 1 Step 1. Do not
recapture the pre-existing inventory and do not invoke `case adopt` again; the
authoritative baseline is the immutable inventory artifact created by Task 2.
Reload the outstanding packet from the case rather than relying on an
in-memory variable:

```powershell
$IntentShowStdout = & $Cli intent show --case-dir $CaseDir
if ($LASTEXITCODE -ne 0) { throw 'OUTSTANDING_INTENT_SHOW_FAILED' }
$IntentShow = $IntentShowStdout | ConvertFrom-Json
$IntentState = Get-EvidenceRecord $IntentShow 'analysis-intent'
$ApprovalPacket = $IntentState.outstanding_approval_request
if ($IntentShow.case_state -ne 'INTENT_DRAFTED' -or
    $null -eq $ApprovalPacket) {
    throw 'OUTSTANDING_APPROVAL_STATE_CHANGED'
}
```

After the user responds, use `apply_patch` to create a unique approval-input
file under `90_Temporary/harness-e2e`. Copy the user's text verbatim and copy
the exact runtime values from the reloaded `$ApprovalPacket`; do not
reconstruct them:

```json
{
  "schema_version": 1,
  "purpose": "analysis-intent",
  "request_id": "RUNTIME_EXACT_REQUEST_ID",
  "analysis_id": "2026-07-30_0729C_local040-screw",
  "revision": 1,
  "contract_sha256": "RUNTIME_EXACT_CONTRACT_SHA256",
  "input_record_set_digest": "RUNTIME_EXACT_INPUT_RECORD_SET_DIGEST",
  "source_selection_approval_record_digest": null,
  "execution_profile_sha256": "RUNTIME_EXACT_EXECUTION_PROFILE_SHA256",
  "nonce": "RUNTIME_EXACT_NONCE",
  "approval_text": "VERBATIM_SUBSEQUENT_USER_TEXT",
  "actor_kind": "human",
  "source_channel": "codex-task",
  "approved_at": "RUNTIME_CURRENT_UTC_TIMESTAMP"
}
```

The `RUNTIME_*` labels above are mandatory runtime substitution markers.
Populate them directly from `$ApprovalPacket`. Before invoking
the CLI, compare every populated value to the approval-request JSON retained on
disk.

```powershell
$ApprovalInput = Get-ChildItem -LiteralPath (Join-Path $CaseDir '90_Temporary\harness-e2e') `
    -Filter 'analysis-intent-approval-*.json' |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
$ApproveStdout = & $Cli intent approve --case-dir $CaseDir --approval-json $ApprovalInput.FullName
if ($LASTEXITCODE -ne 0) {
    throw "INTENT_APPROVAL_FAILED: exit=$LASTEXITCODE"
}
$Approve = $ApproveStdout | ConvertFrom-Json
if ($Approve.case_state -ne 'INTENT_APPROVED') {
    throw "UNEXPECTED_APPROVED_STATE: $($Approve.case_state)"
}
```

Expected: the exact user response is hash-bound and state is `INTENT_APPROVED`. A rejection leaves the case waiting and terminates this E2E.

## Task 5: Adopt the immutable model and pass locked preflight

**Files:**

- Create: one adopted FEB under `02_Model`
- Create: one new attempt and `attempt/solver/input.feb` under `90_Temporary`
- Create: normalized config, tool fingerprints, and preflight evidence
- Modify atomically: `CASE_MANIFEST.json`

**Interfaces:**

- Consumes: `febio-cae model adopt-existing` and `preflight`.
- Produces: `MODEL_BUILT`, then `PREFLIGHT_PASSED`, with no solver process launched during preflight.

- [ ] **Step 1: Create the exact run configuration**

Use `apply_patch` to create
`90_Temporary/harness-e2e/run-config.json`:

Before applying the patch, require `Test-Path -LiteralPath $RunConfig` to be
false. `apply_patch` must add, never update, this execution request.

```powershell
$RunConfig = Join-Path $CaseDir `
    '90_Temporary\harness-e2e\run-config.json'
if (Test-Path -LiteralPath $RunConfig) {
    throw 'EXECUTION_REQUEST_ALREADY_EXISTS'
}
```

```json
{
  "schema_version": 1,
  "timeout_seconds": 1800,
  "cancel_grace_seconds": 15,
  "maximum_process_tree_working_set_mib": 8192,
  "retry_budget": 0,
  "expected_log_times": [
    0.05, 0.10, 0.15, 0.20, 0.25,
    0.30, 0.35, 0.40, 0.45, 0.50,
    0.55, 0.60, 0.65, 0.70, 0.75,
    0.80, 0.85, 0.90, 0.95, 1.00
  ],
  "expected_fbs_times": [
    0.00, 0.05, 0.10, 0.15, 0.20,
    0.25, 0.30, 0.35, 0.40, 0.45,
    0.50, 0.55, 0.60, 0.65, 0.70,
    0.75, 0.80, 0.85, 0.90, 0.95,
    1.00
  ],
  "log_time_abs_tol": 1e-9,
  "fbs_time_abs_tol": 1e-6,
  "displacement_abs_tol_mm": 1e-6,
  "allow_zero_state": false,
  "allow_unrequested_states": false,
  "expected_solver": {
    "path": "C:\\Program Files\\FEBioStudio\\bin\\febio4.exe",
    "sha256": "03B9DB12C4B3E2ED0CF027BE6B8B5D0CEF2D4EDC9EAB26F5A2BD8193EFB770C9",
    "log_version": "4.12.0"
  },
  "expected_fbs_runtime_tree_sha256": "1CD91A25E4E4E4B9454E2AAFFFBB9736B63534B9CA3D2807A0250EC77C869BD0"
}
```

Before model adoption, compute the same digest through the installed canonical
loader and reject any changed field:

```powershell
$Task5VersionStdout = & $Cli --version
if ($LASTEXITCODE -ne 0) {
    throw 'TASK5_INSTALLED_IDENTITY_FAILED'
}
$Task5InstalledIdentity = Get-EvidenceRecord (
    $Task5VersionStdout | ConvertFrom-Json
) 'installed-identity'
$Task5PackageRoot = [System.IO.Path]::GetFullPath(
    [string]$Task5InstalledIdentity.package_path
)
$Task5InstalledVenv = Split-Path (
    Split-Path (
        Split-Path $Task5PackageRoot -Parent
    ) -Parent
) -Parent
$InstalledPython = Join-Path $Task5InstalledVenv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $InstalledPython -PathType Leaf)) {
    throw 'TASK5_INSTALLED_PYTHON_MISSING'
}
$ExecutionRequestSha256 = & $InstalledPython -I -c @'
from pathlib import Path
import sys
from febio_cae_harness.execution_request import load_execution_request
print(load_execution_request(Path(sys.argv[1])).sha256)
'@ $RunConfig
if ($LASTEXITCODE -ne 0 -or
    $ExecutionRequestSha256.Trim() -ne
    'CFBAACD6E66BE20BB6DEE9313D01A5D2FD3D5440B2DF53221A4CFE48F520D11D') {
    throw 'EXECUTION_REQUEST_APPROVAL_DIGEST_MISMATCH'
}
```

- [ ] **Step 2: Adopt the exact FEB create-new**

```powershell
$ModelStdout = & $Cli model adopt-existing --case-dir $CaseDir
if ($LASTEXITCODE -ne 0) {
    throw "MODEL_ADOPTION_FAILED: exit=$LASTEXITCODE"
}
$Model = $ModelStdout | ConvertFrom-Json
$ModelAdoption = Get-EvidenceRecord $Model 'model-adoption'
if ($Model.case_state -ne 'MODEL_BUILT' -or
    $ModelAdoption.source_sha256 -ne
    '6351FC75C98BA4830AFC020B1A381A7755775F2BD4C65823477DD9E216070049' -or
    $ModelAdoption.destination_sha256 -ne $ModelAdoption.source_sha256) {
    throw 'ADOPTED_MODEL_IDENTITY_MISMATCH'
}
```

- [ ] **Step 3: Run preflight and assert every immutable identity**

```powershell
$RunConfig = Join-Path $CaseDir '90_Temporary\harness-e2e\run-config.json'
$PreflightStdout = & $Cli preflight --case-dir $CaseDir --config-json $RunConfig
if ($LASTEXITCODE -ne 0) {
    throw "PREFLIGHT_FAILED: exit=$LASTEXITCODE"
}
$Preflight = $PreflightStdout | ConvertFrom-Json
$PreflightEvidence = Get-EvidenceRecord $Preflight 'preflight'
if ($Preflight.case_state -ne 'PREFLIGHT_PASSED' -or
    $PreflightEvidence.status -ne 'accepted' -or
    [string]::IsNullOrWhiteSpace([string]$PreflightEvidence.attempt_id)) {
    throw "UNEXPECTED_PREFLIGHT_STATE: $($Preflight.case_state)"
}

$ExpectedRoles = @(
    'adopted-feb',
    'staged-feb',
    'model-evidence',
    'intent-approval',
    'solver',
    'policy',
    'fbs-runtime-tree',
    'fbs-loaded-dll-profile',
    'resume-key'
)
if (Compare-Object $ExpectedRoles @($PreflightEvidence.checked_roles)) {
    throw 'PREFLIGHT_ROLE_INVENTORY_MISMATCH'
}
$EvidenceByRole = @{}
foreach ($Record in @($PreflightEvidence.evidence)) {
    if ($EvidenceByRole.ContainsKey([string]$Record.role)) {
        throw "PREFLIGHT_DUPLICATE_ROLE: $($Record.role)"
    }
    $EvidenceByRole[[string]$Record.role] = $Record
}
if (Compare-Object $ExpectedRoles @($EvidenceByRole.Keys)) {
    throw 'PREFLIGHT_EVIDENCE_ROLE_MISMATCH'
}
foreach ($Role in $ExpectedRoles[0..5]) {
    $Record = $EvidenceByRole[$Role]
    if (-not (Test-Path -LiteralPath $Record.path -PathType Leaf)) {
        throw "PREFLIGHT_FILE_MISSING: $Role"
    }
    $Item = Get-Item -LiteralPath $Record.path
    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Record.path).Hash
    if ($Item.Length -ne $Record.bytes -or $Hash -ne $Record.sha256) {
        throw "PREFLIGHT_FILE_IDENTITY_MISMATCH: $Role"
    }
}
$AuthoritativeFebSha256 =
    '6351FC75C98BA4830AFC020B1A381A7755775F2BD4C65823477DD9E216070049'
if ($EvidenceByRole['adopted-feb'].sha256 -ne $AuthoritativeFebSha256 -or
    $EvidenceByRole['staged-feb'].sha256 -ne $AuthoritativeFebSha256) {
    throw 'SOLVER_STAGING_HASH_MISMATCH'
}
$ModelEvidence = Get-Content -LiteralPath (
    [string]$EvidenceByRole['model-evidence'].path
) -Encoding UTF8 -Raw | ConvertFrom-Json
if (
    (Compare-Object @('M2_Screw_Indenter') @(
        $ModelEvidence.excluded_domains
    )) -or
    $ModelEvidence.domain_signature_sha256 -ne
        '4859FBEAC85C365F76023B3DB1D47D367185B49502E70B697ED1664783002A40' -or
    $ModelEvidence.node_count -ne 82779 -or
    $ModelEvidence.element_count -ne 49931
) {
    throw 'MODEL_EVIDENCE_INTENT_BINDING_MISMATCH'
}
if ($EvidenceByRole['solver'].sha256 -ne
    '03B9DB12C4B3E2ED0CF027BE6B8B5D0CEF2D4EDC9EAB26F5A2BD8193EFB770C9') {
    throw 'SOLVER_HASH_DRIFT'
}
if ($EvidenceByRole['fbs-runtime-tree'].sha256 -ne
    '1CD91A25E4E4E4B9454E2AAFFFBB9736B63534B9CA3D2807A0250EC77C869BD0') {
    throw 'FBS_RUNTIME_TREE_DRIFT'
}
if ([string]$EvidenceByRole['fbs-loaded-dll-profile'].sha256 -notmatch
    '^[0-9A-F]{64}$' -or
    [string]$EvidenceByRole['resume-key'].sha256 -notmatch '^[0-9A-F]{64}$') {
    throw 'PREFLIGHT_NONFILE_BINDING_INVALID'
}
```

- [ ] **Step 4: Prove preflight launched no solver process**

```powershell
$UnexpectedProcessEvidence = @(
    $Preflight.evidence | Where-Object { $_.kind -eq 'process-evidence' }
)
if ($UnexpectedProcessEvidence.Count -ne 0) {
    throw 'PROCESS_LAUNCHED_DURING_PREFLIGHT'
}
$SolverDirectory = Split-Path -Parent $EvidenceByRole['staged-feb'].path
foreach ($Leaf in @('solver.log', 'solver.xplt')) {
    if (Test-Path -LiteralPath (Join-Path $SolverDirectory $Leaf)) {
        throw "SOLVER_OUTPUT_EXISTS_DURING_PREFLIGHT: $Leaf"
    }
}
```

Expected: adopted FEB and attempt-staged FEB are byte-identical to the
authoritative source; the closed preflight decision binds all nine roles and
has not launched a process. The solve command reopens and pins the executable
and staged FEB immediately before `CreateProcessW`.

## Task 6: Run a new owned FEBio nonlinear solve

**Files:**

- Create in the attempt solver directory: `solver.log`, `solver.xplt`, `stdout.raw`, `stderr.raw`, and optional partial dump
- Create: process/job/telemetry evidence and attempt result
- Modify atomically: `CASE_MANIFEST.json`

**Interfaces:**

- Consumes: `febio-cae solve --case-dir`.
- Produces: a newly created, normal-termination 20-step result and `SOLVED`, or a fail-closed terminal state with no promotion.

- [ ] **Step 1: Launch only through the installed harness**

```powershell
$SolveStartedUtc = [DateTime]::UtcNow
$SolveStdout = & $Cli solve --case-dir $CaseDir
$SolveExit = $LASTEXITCODE
$SolveFinishedUtc = [DateTime]::UtcNow
$Solve = $SolveStdout | ConvertFrom-Json
```

Allow up to 1800 seconds. Keep the Codex task alive with progress checks that
query only `febio-cae case status --case-dir $CaseDir`; do not start a second
solve or kill an unverified PID.

- [ ] **Step 2: Require owned-process success**

```powershell
if ($SolveExit -ne 0) {
    if ($Solve.allowed_next_actions -contains 'diagnose') {
        & $Cli diagnose --case-dir $CaseDir | Out-Host
    }
    throw "BOTTOMFRAME_SOLVE_FAILED_CLOSED: exit=$SolveExit state=$($Solve.case_state)"
}
if ($Solve.case_state -ne 'SOLVED') { throw 'SOLVE_DID_NOT_REACH_SOLVED' }
$ProcessEvidence = Get-EvidenceRecord $Solve 'process-evidence'
$LogVerification = Get-EvidenceRecord $Solve 'log-verification'
$LogArtifact = Get-ArtifactRecord $Solve 'attempt-solver-log'
$XpltArtifact = Get-ArtifactRecord $Solve 'attempt-solver-xplt'
if ($ProcessEvidence.exit_code -ne 0) { throw 'FEBIO_NONZERO_EXIT' }
if (-not $ProcessEvidence.job_object_assigned) { throw 'JOB_OBJECT_NOT_ASSIGNED' }
if ($ProcessEvidence.active_processes_after_finalize -ne 0) {
    throw 'OWNED_PROCESS_TREE_NOT_EMPTY'
}
if ($ProcessEvidence.maximum_working_set_mib -gt 8192) {
    throw 'MEMORY_BUDGET_EXCEEDED'
}
$ExpectedArgv = @(
    'C:\Program Files\FEBioStudio\bin\febio4.exe',
    '-i', 'input.feb',
    '-o', 'solver.log',
    '-p', 'solver.xplt'
)
if (Compare-Object $ExpectedArgv @($ProcessEvidence.argv)) {
    throw 'FEBIO_ARGV_MISMATCH'
}
if ((Split-Path -Leaf $ProcessEvidence.cwd) -ne 'solver' -or
    $ProcessEvidence.executable_sha256 -ne
    '03B9DB12C4B3E2ED0CF027BE6B8B5D0CEF2D4EDC9EAB26F5A2BD8193EFB770C9' -or
    $ProcessEvidence.input_sha256 -ne
    '6351FC75C98BA4830AFC020B1A381A7755775F2BD4C65823477DD9E216070049') {
    throw 'PINNED_SOLVE_IDENTITY_MISMATCH'
}
```

- [ ] **Step 3: Require a fresh LOG and XPLT from this process**

```powershell
$ArtifactsByKey = @{
    log = $LogArtifact
    xplt = $XpltArtifact
}
foreach ($Key in @('log', 'xplt')) {
    $Artifact = $ArtifactsByKey[$Key]
    $Before = $ProcessEvidence.artifacts_before.$Key
    $After = $ProcessEvidence.artifacts_after.$Key
    if ($Before.exists -ne $false -or $After.exists -ne $true) {
        throw "SOLVER_ARTIFACT_FRESHNESS_MISMATCH: $Key"
    }
    if (-not (Test-Path -LiteralPath $Artifact.path -PathType Leaf)) {
        throw "SOLVER_ARTIFACT_MISSING: $($Artifact.path)"
    }
    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Artifact.path).Hash
    if ($Hash -ne $Artifact.sha256 -or
        $After.path -ne $Artifact.path -or
        $After.bytes -ne $Artifact.bytes -or
        $After.sha256 -ne $Artifact.sha256) {
        throw "SOLVER_ARTIFACT_HASH_MISMATCH: $($Artifact.path)"
    }
}
if ([DateTime]$ProcessEvidence.ended_at -lt
    [DateTime]$ProcessEvidence.started_at) {
    throw 'PROCESS_TIME_ORDER_INVALID'
}
if ($LogArtifact.path -eq
    'C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\normal-solve.log' -or
    $XpltArtifact.path -eq
    'C:\dev\FEBio\jobs\0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm\normal-solve.xplt') {
    throw 'HISTORICAL_RESULT_REUSED'
}
```

- [ ] **Step 4: Require composite LOG completion**

```powershell
$L = $LogVerification
if ($L.status -ne 'accepted' -or
    $L.solver_version -ne '4.12.0' -or
    $L.files_used.input -ne 'input.feb' -or
    $L.files_used.log -ne 'solver.log' -or
    $L.files_used.plot -ne 'solver.xplt') {
    throw 'LOG_IDENTITY_MISMATCH'
}
$ExpectedConvergedTimes = @(1..20 | ForEach-Object { [double]$_ / 20.0 })
if ($L.completed_steps -ne 20 -or
    @($L.beginning_steps).Count -ne 20 -or
    @($L.converged_times).Count -ne 20 -or
    @($L.converged_line_indices).Count -ne 20 -or
    -not $L.normal_termination -or
    $null -eq $L.normal_termination_line_index -or
    $L.normal_termination_line_index -le $L.converged_line_indices[-1]) {
    throw 'LOG_INCOMPLETE'
}
for ($Index = 0; $Index -lt $ExpectedConvergedTimes.Count; $Index++) {
    if ([math]::Abs(
        [double]$L.converged_times[$Index] -
        $ExpectedConvergedTimes[$Index]
    ) -gt 1e-9) {
        throw "LOG_CONVERGED_TIME_MISMATCH: index=$Index"
    }
}
if (@($L.error_lines).Count -ne 0 -or
    @($L.unresolved_warnings).Count -ne 0 -or
    @($L.blocking_reasons).Count -ne 0) {
    throw 'LOG_CONTAINS_BLOCKING_DIAGNOSTIC'
}
```

Expected: a new process exits zero, all 20 fixed steps converge to `t=1.0`, normal termination follows the final marker, and no blocker remains.

## Task 7: Verify the new XPLT with the locked official FBS bridge

**Files:**

- Create: FBS bridge request/response and result-verification evidence
- Modify atomically: `CASE_MANIFEST.json`
- Promote nothing until every verification in this Task passes

**Interfaces:**

- Consumes: `febio-cae verify --case-dir`.
- Produces: `RESULT_VERIFIED` and a completion decision bound to the new attempt hashes.

- [ ] **Step 1: Run the composite verifier**

```powershell
$VerifyStdout = & $Cli verify --case-dir $CaseDir
if ($LASTEXITCODE -ne 0) {
    throw "BOTTOMFRAME_VERIFY_FAILED: exit=$LASTEXITCODE"
}
$Verify = $VerifyStdout | ConvertFrom-Json
$VerifiedXpltArtifact = Get-ArtifactRecord $Verify 'attempt-solver-xplt'
if ($Verify.case_state -ne 'RESULT_VERIFIED') {
    throw "UNEXPECTED_VERIFY_STATE: $($Verify.case_state)"
}
```

- [ ] **Step 2: Verify the FBS identity and state set**

```powershell
$F = Get-EvidenceRecord $Verify 'fbs-verification'
if ($F.status -ne 'accepted' -or
    $F.xplt.bytes -ne $VerifiedXpltArtifact.bytes -or
    $F.xplt.sha256 -ne $VerifiedXpltArtifact.sha256 -or
    $F.parent_pre_worker_xplt_sha256 -ne $VerifiedXpltArtifact.sha256 -or
    $F.worker_xplt_sha256 -ne $VerifiedXpltArtifact.sha256 -or
    $F.parent_post_worker_xplt_sha256 -ne $VerifiedXpltArtifact.sha256) {
    throw 'FBS_XPLT_TOCTOU'
}
if ($F.state_count -ne 21 -or
    @($F.state_times).Count -ne 21 -or
    @($F.state_matches).Count -ne 21 -or
    $F.nonzero_target_states -ne 20 -or
    $F.unrequested_state_count -ne 0 -or
    $F.zero_state_count -ne 1 -or
    [math]::Abs([double]$F.final_time - 1.0) -gt 1e-6) {
    throw 'FBS_STATE_SET_MISMATCH'
}
if ($F.maximum_target_time_abs_error -gt 1e-6) {
    throw 'FBS_TARGET_TIME_ERROR'
}
```

- [ ] **Step 3: Verify connectivity, coordinates, fields, and populations**

```powershell
$R = Get-EvidenceRecord $Verify 'result-validation'
if ($R.status -ne 'accepted' -or
    $R.case_state -ne 'RESULT_VERIFIED' -or
    @($R.blocking_reasons).Count -ne 0 -or
    $R.node_count -ne 82779 -or
    $R.element_count -ne 49931 -or
    -not $R.connectivity_signature_match -or
    -not $R.binary32_initial_coordinate_exact_match -or
    -not $R.populations_disjoint) {
    throw 'FBS_MODEL_IDENTITY_MISMATCH'
}
if ($R.input_feb_sha256_before -ne
    '6351FC75C98BA4830AFC020B1A381A7755775F2BD4C65823477DD9E216070049' -or
    $R.input_feb_sha256_after -ne $R.input_feb_sha256_before -or
    $R.domain_signature_before -ne
    '4859FBEAC85C365F76023B3DB1D47D367185B49502E70B697ED1664783002A40' -or
    $R.domain_signature_after -ne $R.domain_signature_before) {
    throw 'FBS_MODEL_HASH_BINDING_MISMATCH'
}
if ($R.population_counts_by_partition.Part2.elements -ne 47289 -or
    $R.population_counts_by_partition.Part2.referenced_nodes -ne 82066) {
    throw 'FBS_PART2_COUNT_MISMATCH'
}
if ($R.population_counts_by_partition.M2_Screw_Indenter.elements -ne 2642 -or
    $R.population_counts_by_partition.M2_Screw_Indenter.referenced_nodes -ne 713) {
    throw 'FBS_RIGID_COUNT_MISMATCH'
}
$ExpectedRequests = @(
    'displacement:nodeData:0:M2_Screw_Indenter',
    'displacement:nodeData:1:M2_Screw_Indenter',
    'displacement:nodeData:2:M2_Screw_Indenter',
    'displacement:nodeData:6:M2_Screw_Indenter',
    'stress:elemData:6:Part2',
    'relative volume:elemData:0:Part2'
)
if (Compare-Object $ExpectedRequests @($R.request_keys)) {
    throw 'FBS_REQUEST_INVENTORY_MISMATCH'
}
if ($R.empty_array_count -ne 0 -or
    $R.nonfinite_value_count -ne 0 -or
    $R.ambiguous_collection_count -ne 0) {
    throw 'FBS_ARRAY_COMPLETENESS_FAILURE'
}
```

- [ ] **Step 4: Verify full rigid-vector kinematics, not magnitude alone**

```powershell
if ($R.rigid_kinematics.maximum_component_abs_error_mm -gt 1e-6 -or
    $R.rigid_kinematics.maximum_magnitude_abs_error_mm -gt 1e-6 -or
    $R.rigid_kinematics.maximum_norm_consistency_abs_error_mm -gt 1e-6 -or
    -not $R.rigid_kinematics.all_nodes_all_target_states_checked) {
    throw 'RIGID_KINEMATICS_MISMATCH'
}
```

- [ ] **Step 5: Recheck immutable model identities before promotion**

```powershell
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $SourceFeb).Hash -ne
    '6351FC75C98BA4830AFC020B1A381A7755775F2BD4C65823477DD9E216070049') {
    throw 'AUTHORITATIVE_FEB_CHANGED_DURING_SOLVE'
}
if ($R.input_feb_sha256_before -ne
    $R.input_feb_sha256_after -or
    $R.domain_signature_before -ne
    $R.domain_signature_after) {
    throw 'STAGED_MODEL_CHANGED_DURING_SOLVE'
}
```

Expected: the new XPLT alone passes all requested official-FBS checks and the state reaches `RESULT_VERIFIED`.

## Task 8: Generate, promote, and audit the final report

**Files:**

- Create: verified XPLT under `03_Result`
- Create: JSON and HTML report under `04_Report`
- Create: LOG, process, approval, signature, and completion evidence under `05_Verification`
- Modify atomically: `CASE_MANIFEST.json`
- Append only: harness event log

**Interfaces:**

- Consumes: `febio-cae report --case-dir`.
- Produces: `REPORTED`, create-new promoted artifacts, and a report that distinguishes numerical execution from physical/product validation.

- [ ] **Step 1: Generate and promote the deterministic report**

```powershell
$ReportStdout = & $Cli report --case-dir $CaseDir
if ($LASTEXITCODE -ne 0) {
    throw "BOTTOMFRAME_REPORT_FAILED: exit=$LASTEXITCODE"
}
$Report = $ReportStdout | ConvertFrom-Json
$ReportPromotion = Get-EvidenceRecord $Report 'report-promotion'
$ReportCompletion = Get-EvidenceRecord $Report 'completion-decision'
if ($Report.case_state -ne 'REPORTED') {
    throw "UNEXPECTED_REPORT_STATE: $($Report.case_state)"
}
foreach ($Artifact in $Report.artifacts) {
    if (-not (Test-Path -LiteralPath $Artifact.path -PathType Leaf)) {
        throw "PROMOTED_ARTIFACT_MISSING: $($Artifact.path)"
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Artifact.path).Hash -ne
        $Artifact.sha256) {
        throw "PROMOTED_ARTIFACT_HASH_MISMATCH: $($Artifact.path)"
    }
}
```

- [ ] **Step 2: Assert create-new promotion and completion binding**

```powershell
if ($ReportPromotion.promotions_overwritten_count -ne 0) {
    throw 'PROMOTION_OVERWROTE_EXISTING_FILE'
}
foreach ($PointerName in @(
    'completion_decision',
    'result_verification',
    'promotion_event'
)) {
    $Pointer = $ReportPromotion.$PointerName
    if (-not (Test-Path -LiteralPath $Pointer.path -PathType Leaf) -or
        (Get-Item -LiteralPath $Pointer.path).Length -ne $Pointer.bytes -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $Pointer.path).Hash -ne
        $Pointer.sha256) {
        throw "REPORT_BINDING_POINTER_DRIFT: $PointerName"
    }
}
if ($ReportCompletion.status -ne 'accepted') {
    throw 'REPORT_COMPLETION_NOT_ACCEPTED'
}
$PromotedXplt = @(
    $ReportPromotion.promoted_results |
        Where-Object { $_.role -eq 'xplt' }
)
if ($PromotedXplt.Count -ne 1 -or
    $PromotedXplt[0].source_sha256 -ne
        $PromotedXplt[0].destination_sha256) {
    throw 'PROMOTED_XPLT_SOURCE_MISMATCH'
}
```

- [ ] **Step 3: Assert the scope limitations in both report forms**

```powershell
$JsonReportArtifact = Get-ArtifactRecord $Report 'audit-report-json'
$HtmlReportArtifact = Get-ArtifactRecord $Report 'audit-report-html'
$JsonReport = Get-Content -LiteralPath $JsonReportArtifact.path -Encoding UTF8 -Raw |
    ConvertFrom-Json
$HtmlReport = Get-Content -LiteralPath $HtmlReportArtifact.path -Encoding UTF8 -Raw
$ReportVersionStdout = & $Cli --version
if ($LASTEXITCODE -ne 0) {
    throw 'REPORT_INSTALLED_IDENTITY_FAILED'
}
$ReportInstalledIdentity = Get-EvidenceRecord (
    $ReportVersionStdout | ConvertFrom-Json
) 'installed-identity'
$ReportTools = $JsonReport.provenance.tool_fingerprints
if (
    $ReportTools.build_provenance_sha256 -ne
        $ReportInstalledIdentity.build_provenance.sha256 -or
    $ReportTools.install_manifest_sha256 -ne
        $ReportInstalledIdentity.release.install_manifest.sha256 -or
    $ReportTools.wheel_sha256 -ne
        $ReportInstalledIdentity.wheel.sha256 -or
    $ReportTools.reviewed_source_commit -ne
        $ReportInstalledIdentity.build_provenance.source_commit
) {
    throw 'REPORT_RELEASE_PROVENANCE_DRIFT'
}
$RequiredLimitations = @(
    'ABS material calibration',
    'thread behavior',
    'friction behavior',
    'manufacturing robustness',
    'product strength',
    'design acceptance'
)
foreach ($Limitation in $RequiredLimitations) {
    if ($JsonReport.limitations -notcontains $Limitation) {
        throw "JSON_REPORT_LIMITATION_MISSING: $Limitation"
    }
    if ($HtmlReport -notmatch [regex]::Escape($Limitation)) {
        throw "HTML_REPORT_LIMITATION_MISSING: $Limitation"
    }
}
if ($JsonReport.conclusion.claim_type -ne 'Calculation' -or
    $JsonReport.conclusion.product_acceptance -ne $false) {
    throw 'REPORT_OVERCLAIMS_VALIDATION'
}
```

Expected: new verified artifacts are promoted create-new, reports are hash-bound, and the conclusion is limited to execution/result-verification success.

## Task 9: Prove pre-existing-file integrity and the no-Git boundary

**Files:**

- Read: all case and all tool-worktree inventories
- Create/Modify: none beyond the declared Task 2–8 harness outputs

**Interfaces:**

- Consumes: `$BeforeCaseInventory`, `$BeforeWorktreeInventory`, final manifest/events, release boundary policy.
- Produces: final E2E acceptance result and evidence paths; no commit.

- [ ] **Step 1: Compare every pre-existing case file**

```powershell
$PersistedInventoryFiles = @(
    Get-ChildItem -LiteralPath (
        Join-Path $CaseDir '05_Verification\harness\preexisting-inventories'
    ) -File -Filter 'preexisting-inventory-*.json'
)
if ($PersistedInventoryFiles.Count -ne 1) {
    throw "PERSISTED_PREEXISTING_INVENTORY_COUNT: $($PersistedInventoryFiles.Count)"
}
$PersistedInventory = Get-Content -LiteralPath $PersistedInventoryFiles[0].FullName `
    -Encoding UTF8 -Raw | ConvertFrom-Json
$BeforeCaseInventory = @($PersistedInventory.files)

$AfterByPath = @{}
Get-ChildItem -LiteralPath $CaseDir -Recurse -Force -File |
    ForEach-Object {
        $Relative = $_.FullName.Substring($CaseDir.Length + 1).Replace('\','/')
        $AfterByPath[$Relative] = [pscustomobject]@{
            bytes = $_.Length
            sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
        }
    }

foreach ($Before in $BeforeCaseInventory) {
    if (-not $AfterByPath.ContainsKey($Before.relative_path)) {
        throw "PREEXISTING_FILE_MISSING: $($Before.relative_path)"
    }
    if ($Before.relative_path -eq 'CASE_MANIFEST.json') {
        continue
    }
    $After = $AfterByPath[$Before.relative_path]
    if ($After.bytes -ne $Before.bytes -or $After.sha256 -ne $Before.sha256) {
        throw "PREEXISTING_FILE_CHANGED: $($Before.relative_path)"
    }
}
$LegacyManifestRows = @(
    $BeforeCaseInventory |
        Where-Object { $_.relative_path -eq 'CASE_MANIFEST.json' }
)
if ($LegacyManifestRows.Count -ne 1) {
    throw 'LEGACY_MANIFEST_INVENTORY_ROW_MISSING'
}
$LegacyBackup = Join-Path $CaseDir (
    '05_Verification\harness\legacy-manifests\legacy-manifest-' +
    $LegacyManifestRows[0].sha256 + '.json'
)
if (-not (Test-Path -LiteralPath $LegacyBackup -PathType Leaf) -or
    (Get-Item -LiteralPath $LegacyBackup).Length -ne
        $LegacyManifestRows[0].bytes -or
    (Get-FileHash -Algorithm SHA256 -LiteralPath $LegacyBackup).Hash -ne
        $LegacyManifestRows[0].sha256) {
    throw 'LEGACY_MANIFEST_BACKUP_MISMATCH'
}
$IntegrityStatusStdout = & $Cli case status --case-dir $CaseDir
if ($LASTEXITCODE -ne 0) {
    throw 'EVENT_CHAIN_OR_MANIFEST_REPLAY_FAILED'
}
$IntegrityStatus = $IntegrityStatusStdout | ConvertFrom-Json
$IntegrityEvidence = Get-EvidenceRecord $IntegrityStatus 'case-status'
if (-not $IntegrityEvidence.event_chain_valid -or
    -not $IntegrityEvidence.manifest_projection_current -or
    $IntegrityEvidence.event_count -lt 1) {
    throw 'CASE_INTEGRITY_AUDIT_FAILED'
}
```

Phase 1A legacy adoption rejects any pre-existing harness-owned event/attempt
path, so no event prefix can be silently inherited. The successful status
replay above is the executable hash-chain and manifest-projection audit.

- [ ] **Step 2: Verify final state and no active process**

```powershell
$StatusStdout = & $Cli case status --case-dir $CaseDir
if ($LASTEXITCODE -ne 0) { throw 'FINAL_STATUS_FAILED' }
$FinalStatus = $StatusStdout | ConvertFrom-Json
$CaseStatusEvidence = Get-EvidenceRecord $FinalStatus 'case-status'
if ($FinalStatus.case_state -ne 'REPORTED') { throw 'FINAL_STATE_NOT_REPORTED' }
if ($CaseStatusEvidence.active_run_lease -ne $null -or
    $CaseStatusEvidence.active_process_count -ne 0) {
    throw 'FINAL_ACTIVE_PROCESS_OR_LEASE'
}
```

- [ ] **Step 3: Audit every tool worktree and package inventory**

```powershell
$WorktreeInventoryPath = Join-Path (
    Join-Path ([System.IO.Path]::GetTempPath()) `
        'FEBioCaeHarness\phase1e-20260730-bottomframe'
) 'preexisting-worktree-inventory.json'
if (-not (Test-Path -LiteralPath $WorktreeInventoryPath -PathType Leaf)) {
    throw 'PREEXISTING_WORKTREE_INVENTORY_MISSING'
}
$BeforeWorktreeInventory = @(
    (
        Get-Content -LiteralPath $WorktreeInventoryPath -Encoding UTF8 -Raw |
            ConvertFrom-Json
    ).worktrees
)
$WorktreeLines = & git -C $ToolRoot worktree list --porcelain
$WorktreePaths = @(
    $WorktreeLines |
        Where-Object { $_ -like 'worktree *' } |
        ForEach-Object { $_.Substring(9) }
)
$AfterWorktreeInventory = @(
    foreach ($WorktreePath in $WorktreePaths) {
        $CanonicalWorktreePath = [System.IO.Path]::GetFullPath(
            $WorktreePath
        )
        $CanonicalCaeRoot = [System.IO.Path]::GetFullPath(
            $CaeRoot
        ).TrimEnd('\')
        if (
            $CanonicalWorktreePath -ieq $CanonicalCaeRoot -or
            $CanonicalWorktreePath.StartsWith(
                $CanonicalCaeRoot + '\',
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "TOOL_WORKTREE_INSIDE_CAE_TREE: $CanonicalWorktreePath"
        }
        [pscustomobject]@{
            path = $CanonicalWorktreePath
            status = @(& git -C $CanonicalWorktreePath status --porcelain=v1 --untracked-files=all)
            ignored = @(& git -C $CanonicalWorktreePath ls-files --others --ignored --exclude-standard)
        }
    }
)
$BeforeWorktreeJson = $BeforeWorktreeInventory |
    ConvertTo-Json -Depth 8 -Compress
$AfterWorktreeJson = $AfterWorktreeInventory |
    ConvertTo-Json -Depth 8 -Compress
if ($AfterWorktreeJson -cne $BeforeWorktreeJson) {
    throw 'TOOL_WORKTREE_INVENTORY_CHANGED_BY_REAL_E2E'
}

$VersionResult = (& $Cli --version) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'FINAL_INSTALLED_IDENTITY_FAILED' }
$InstalledIdentity = Get-EvidenceRecord $VersionResult 'installed-identity'
$PackageRoot = [System.IO.Path]::GetFullPath(
    [string]$InstalledIdentity.package_path
)
$InstalledVenv = Split-Path (
    Split-Path (
        Split-Path $PackageRoot -Parent
    ) -Parent
) -Parent
$InstalledPython = Join-Path $InstalledVenv 'Scripts\python.exe'
$PolicyPath = [string]$InstalledIdentity.release.repository_policy.path
$Archives = @($InstalledIdentity.release.archives)
$ArchivePaths = @($Archives | ForEach-Object { [string]$_.path })
foreach ($Pointer in @(
    $InstalledIdentity.release.install_manifest,
    $InstalledIdentity.release.release_manifest,
    $InstalledIdentity.release.repository_policy
) + $Archives) {
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Pointer.path).Hash -ne
        $Pointer.sha256) {
        throw "FINAL_RELEASE_POINTER_DRIFT: $($Pointer.path)"
    }
}
foreach ($WorktreePath in $WorktreePaths) {
    $BoundaryJson = & $InstalledPython `
        -m febio_cae_harness.repository_boundary `
        --repo $WorktreePath `
        --policy $PolicyPath `
        --expected-policy-sha256 $InstalledIdentity.release.repository_policy.sha256 `
        --archives $ArchivePaths
    $BoundaryExit = $LASTEXITCODE
    $Boundary = $BoundaryJson | ConvertFrom-Json
    if ($BoundaryExit -ne 0 -or
        $Boundary.status -ne 'accepted' -or
        @($Boundary.violations).Count -ne 0) {
        throw "REPOSITORY_BOUNDARY_REJECTED: worktree=$WorktreePath"
    }
}
$FinalToolStatus = & git -C $ToolRoot status --short
if ($FinalToolStatus) {
    throw "TOOL_REPOSITORY_CHANGED_BY_REAL_E2E: $($FinalToolStatus -join ', ')"
}
```

The installed release's scanner has now audited each worktree's independent
HEAD/index/worktree/ignored bytes and the installed wheel/sdist. Require no new
real `.feb`, `.xplt`, STEP/STP, production LOG, external source path, or
BottomFrame identifier; only the pre-reviewed hash-bound historical text
allowlist may match in source documentation.

- [ ] **Step 4: Record the final evidence for handoff**

Report:

- installed release ID, wheel hash, build-provenance hash, Skill hash, solver hash/version, and FBS runtime/profile hashes;
- authoritative, adopted, and staged FEB paths/bytes/hashes;
- attempt ID, process exit, wall time, aggregate peak working set, LOG/XPLT paths/bytes/hashes;
- 20/20 target states, normal termination, warning classification, FBS field/component/population counts, connectivity/coordinate signatures, and rigid-vector maximum errors;
- promoted result/evidence/report paths and hashes;
- immutable pre-existing-file comparison and all-worktree no-Git-leak result;
- explicit physical/product-validation limitations.

Expected: all gates pass, final state is `REPORTED`, no owned process remains, the tool repository is clean, and every real artifact stays under `02_CAE`.

## Failure recovery

- If a source, approval, preflight, solve, verification, or report gate fails, preserve the attempt and evidence exactly as created. Do not delete or overwrite it.
- Run `febio-cae diagnose --case-dir $CaseDir` only when it is an allowed next
  action. Because `allowed_numerical_changes=[]` and the execution
  `retry_budget=0`, diagnosis may explain but must not mutate or retry.
- If the defect is in reusable harness code, leave the real case stopped, return to the isolated tool worktree, add a synthetic regression test, observe RED, implement and commit the fix, rebuild a new content-addressed release, reinstall it, reverify hashes, then start a new real attempt. Never reuse the old wheel or partial attempt.
- If the failure concerns the model or Analysis Intent, stop for human review. Any revised contract receives revision `N+1` and a fresh nonce-bound approval.
- Never remove failed artifacts. Move nothing to `98_Delete_Review` unless a separate cleanup plan is reviewed and explicitly approved.

## Final acceptance checklist

- [ ] Exact aligned FEB hash used; prohibited predecessor never selected.
- [ ] Fresh Analysis Intent approval recorded after the approval request.
- [ ] No model, attempt, or process existed before approval.
- [ ] Adopted/staged FEB and model signatures remained unchanged.
- [ ] A new owned FEBio 4.12 process completed 20 fixed steps to `t=1.0`.
- [ ] New LOG/XPLT passed process, LOG, freshness, and official-FBS gates.
- [ ] All requested node/element fields, populations, states, and rigid-vector checks passed.
- [ ] Resume key and report bind the installed build, wheel, install manifest, and reviewed source commit.
- [ ] Verified artifacts and reports were promoted create-new only.
- [ ] Pre-existing files other than manifest projection and append-only events retained their hashes.
- [ ] Tool repository/worktrees/packages contain no new real CAE data.
- [ ] Final state is `REPORTED`; report makes no physical/product acceptance claim.
