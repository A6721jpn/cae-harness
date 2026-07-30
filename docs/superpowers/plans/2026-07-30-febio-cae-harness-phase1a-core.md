# FEBio CAE Harness Phase 1A Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the audited Phase 1A core that creates a safe CAE case, records immutable input provenance, inspects FEB/STEP sources, drafts a complete Analysis Intent Contract, and reaches `INTENT_APPROVED` only after a nonce-bound human approval.

**Architecture:** `apps/febio_cae_harness` is an independent Python 3.12 package. The core separates canonical JSON/hashing, workspace and immutable-write policy, state/event persistence, source provenance, read-only inspection, inheritance, intent validation, and approval binding behind typed interfaces; later Phase 1 plans consume these interfaces without importing `febio_gmsh_launcher`.

**Tech Stack:** Python 3.12, setuptools 83.0.0, build 1.5.0, jsonschema 4.26.0, psutil 7.2.2, pytest 9.1.1, optional Gmsh 4.15.2, Windows file locking and `MoveFileExW`, PowerShell 5.1.

## Global Constraints

- Acceptance contract: `docs/superpowers/plans/2026-07-30-febio-llm-cae-harness-phase1.md`.
- Approved design: `docs/superpowers/specs/2026-07-30-febio-llm-cae-harness-design.md`.
- Acceptance pin: Setup Gate 0 must verify the master and Phase 1A–1E plans from one plan-suite commit and record that commit, every Git blob ID, and SHA-256/byte count of each exact committed blob byte stream; an authoring-time master hash is intentionally not frozen here.
- Approved-design SHA-256: `B9AFD8DD8AF5CF2318D5DF90AFB018C82EBE97B6D13B36CFEADAF6354D9D8D48`.
- Implement only in branch `codex/febio-cae-harness-phase1` and worktree `C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\_worktrees\febio-cae-harness-phase1`.
- Read the worktree `AGENTS.md` before edits. Reusable code, tests, synthetic fixtures, and documentation stay under `01_Tools/febio-tools`; real CAE bytes stay under `02_CAE`.
- Do not modify `apps/febio_gmsh_launcher`; Phase 1A may run its regression suite only.
- Do not add a Git remote. A later handoff may add or use only a user-approved tool-only remote.
- Never overwrite or delete an existing CAE artifact. Only `CASE_MANIFEST.json` may use atomic replacement; every other persistent artifact is create-new.
- Synthetic fixtures must be small and contain no product geometry, Bottom Frame identifiers, production LOG text, or real model values.
- Reject `DOCTYPE` and `ENTITY` in XML before parsing. STEP inspection is read-only: no mesh, heal, export, or FEB compilation.
- CLI stdout is exactly one UTF-8 JSON object. Stable exit codes are `0`, `10`, `20`, `30`, `40`, `50`, `60`, and `70`.
- Approval binding is auditable but cannot cryptographically authenticate the speaker. Code must never synthesize approval text.
- Every implementation Task follows RED, exact RED observation, minimal implementation, focused GREEN, relevant regression, `git status --short`, exact `git add`, and one commit.
- Treat every checkbox as one 2–5 minute copy/run/observe unit; do not combine adjacent checkboxes into an unreviewed batch.
- Each Task that adds a schema updates `tests/contract/test_installed_resources.py` in the same commit.

## File and responsibility map

| Area | Files | Responsibility |
|---|---|---|
| Package contract | `pyproject.toml`, `errors.py`, `response.py`, `schema.py`, `cli.py` | Installed package metadata, response envelope, exit codes, schema loading, minimal JSON CLI |
| IO policy | `hashing.py`, `jsonio.py`, `workspace.py` | Canonical bytes, SHA-256, manifest replacement, immutable create-new writes, path/Git boundary |
| Formal state | `case_state.py`, `events.py`, `locks.py`, `case_store.py` | Guarded transitions, hash-chained events, Windows writer lock, authoritative replay and projection |
| Input authority | `provenance.py`, `input_store.py` | Exact source resolution, immutable provenance records, separate source-selection approval |
| Inspection | `feb_inspector.py`, `step_inspector.py`, `inheritance.py` | Read-only FEB/STEP inventory, reference closure, signature/geometry evidence, inheritance report |
| Intent and approval | `intent.py`, `approval.py` | Priority resolution, intent schema/validation/revisions, nonce-bound human approval |

## Mandatory Execution Index

The sections are grouped by acceptance surface, but dependencies are strict.
Execute IDs in this order and do not skip forward:

```text
Setup Gate 0
Task 1 -> Task 2 -> Task 3 -> Task 4 -> Task 5 -> Task 6 -> Task 7
Task 8 -> Task 9 -> Task 10 -> Task 11 -> Task 12 -> Task 13 -> Task 14
```

---

### Setup Gate 0: Create and verify the isolated implementation worktree

**Files:**

- Read: `AGENTS.md`
- Read: `docs/superpowers/plans/2026-07-30-febio-llm-cae-harness-phase1.md`
- Read: `docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1a-core.md`
- Read: `docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1b-runner-evidence.md`
- Read: `docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1c-orchestration-report.md`
- Read: `docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1d-codex-release.md`
- Read: `docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1e-real-model-e2e.md`
- Create: `docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1-approved-suite.json`
- Create: `docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1-boundary-review.json`

**Interfaces:**

- Consumes: canonical repository path, committed acceptance plan, committed Phase 1A plan, bootstrap Python 3.12.
- Produces: `$PlanCommit`, its direct child `$ApprovedSuiteCommit`, that commit's direct child `$BoundaryApprovalCommit`, a committed six-file approved-suite manifest, a committed hash-only repository-boundary review manifest, a clean `codex/febio-cae-harness-phase1` worktree, `.venv`, and a recorded launcher-test baseline.

- [ ] **Step 1: Verify all six reviewed plans share one commit and the target does not collide**

```powershell
$ToolRoot = 'C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\febio-tools'
$AcceptancePlan = 'docs/superpowers/plans/2026-07-30-febio-llm-cae-harness-phase1.md'
$ExecutionPlan = 'docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1a-core.md'
$PlanSuite = @(
    $AcceptancePlan,
    $ExecutionPlan,
    'docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1b-runner-evidence.md',
    'docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1c-orchestration-report.md',
    'docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1d-codex-release.md',
    'docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1e-real-model-e2e.md'
)
$Worktree = 'C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\_worktrees\febio-cae-harness-phase1'
$Branch = 'codex/febio-cae-harness-phase1'
$Python312 = 'C:\Users\backo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath $Python312 -PathType Leaf)) {
    throw "Pinned Python is missing: $Python312"
}

$PlanCommit = (git -C $ToolRoot log -1 --format=%H -- $ExecutionPlan).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($PlanCommit)) {
    throw "Phase 1A plan is not committed"
}
$PlanSuiteEvidence = foreach ($PlanPath in $PlanSuite) {
    $PathCommit = (git -C $ToolRoot log -1 --format=%H -- $PlanPath).Trim()
    if ($PathCommit -ne $PlanCommit) {
        throw "Plan is not from plan-suite commit $PlanCommit`: $PlanPath"
    }
    git -C $ToolRoot cat-file -e "$PlanCommit`:$PlanPath"
    if ($LASTEXITCODE -ne 0) {
        throw "Plan blob is absent from plan-suite commit: $PlanPath"
    }
    git -C $ToolRoot diff --quiet $PlanCommit -- $PlanPath
    if ($LASTEXITCODE -ne 0) {
        throw "Working plan differs from plan-suite commit: $PlanPath"
    }
    $Measured = (& $Python312 -c "import hashlib,subprocess,sys; b=subprocess.check_output(['git','-C',sys.argv[1],'show',sys.argv[2]+':'+sys.argv[3]]); print(hashlib.sha256(b).hexdigest().upper(),len(b))" $ToolRoot $PlanCommit $PlanPath).Trim().Split(' ')
    if ($LASTEXITCODE -ne 0 -or $Measured.Count -ne 2) {
        throw "Plan Git-blob measurement failed: $PlanPath"
    }
    [pscustomobject]@{
        path = $PlanPath
        commit = $PlanCommit
        blob = (git -C $ToolRoot rev-parse "$PlanCommit`:$PlanPath").Trim()
        bytes = [long]$Measured[1]
        sha256 = $Measured[0]
    }
}
$PlanSuiteEvidence | Format-Table -AutoSize
if (Test-Path -LiteralPath $Worktree) {
    throw "Target worktree already exists: $Worktree"
}
if (git -C $ToolRoot branch --list $Branch) {
    throw "Target branch already exists: $Branch"
}
if (git -C $ToolRoot remote) {
    throw "A remote is configured; stop for tool-only remote review"
}
```

Expected: exit code `0`, six rows with one identical commit and six nonblank
blob/SHA-256 values, no remote name, and no exception.

- [ ] **Step 2: Create the worktree from the exact reviewed commit**

```powershell
git -C $ToolRoot worktree add $Worktree -b $Branch $PlanCommit
if ($LASTEXITCODE -ne 0) {
    throw "git worktree add failed"
}
Set-Location -LiteralPath $Worktree
$ActualRoot = [IO.Path]::GetFullPath(
    (git rev-parse --show-toplevel).Trim()
)
$ExpectedWorktreeRoot = [IO.Path]::GetFullPath($Worktree)
$ActualBranch = (git branch --show-current).Trim()
$ActualHead = (git rev-parse HEAD).Trim()
if ($ActualRoot -ne $ExpectedWorktreeRoot) {
    throw "Wrong worktree root: $ActualRoot"
}
if ($ActualBranch -ne $Branch) {
    throw "Wrong branch: $ActualBranch"
}
if ($ActualHead -ne $PlanCommit) {
    throw "Wrong base commit: $ActualHead"
}
git status --short --branch
```

Expected:

```text
## codex/febio-cae-harness-phase1
```

- [ ] **Step 3: Prove the approved-suite manifest is absent (RED)**

```powershell
$SuiteManifest = Join-Path $Worktree `
  'docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1-approved-suite.json'
$env:FEBIO_APPROVED_SUITE = $SuiteManifest
& $Python312 -c "import os; from pathlib import Path; Path(os.environ['FEBIO_APPROVED_SUITE']).read_bytes()"
```

Expected: exit code `1` and a traceback ending with:

```text
FileNotFoundError: [Errno 2] No such file or directory
```

- [ ] **Step 4: Create the canonical approved-suite manifest**

```powershell
$PlanEntries = @(
    $PlanSuiteEvidence | ForEach-Object {
        [ordered]@{
            path = $_.path
            sha256 = $_.sha256
            bytes = [int64]$_.bytes
            git_blob = $_.blob
        }
    }
)
$SuiteValue = [ordered]@{
    schema_version = 1
    plan_commit = $PlanCommit
    files = $PlanEntries
}
$SuiteJson = $SuiteValue | ConvertTo-Json -Depth 5 -Compress
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $SuiteManifest,
    $SuiteJson + "`n",
    $Utf8NoBom
)
```

Expected: one UTF-8/LF JSON object; it lists only the master and Phase 1A–1E,
not itself.

- [ ] **Step 5: Validate every approved-suite field against Git and bytes**

```powershell
$LoadedSuite = Get-Content -Raw -LiteralPath $SuiteManifest |
    ConvertFrom-Json
if ($LoadedSuite.schema_version -ne 1) {
    throw "Wrong approved-suite schema version"
}
if ($LoadedSuite.plan_commit -ne $PlanCommit) {
    throw "Approved-suite commit drift"
}
if (@($LoadedSuite.files).Count -ne 6) {
    throw "Approved-suite must contain exactly six plans"
}
if (@($LoadedSuite.files.path | Sort-Object -Unique).Count -ne 6) {
    throw "Approved-suite paths must be unique"
}
if ($LoadedSuite.files.path -contains (
    'docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1-approved-suite.json'
)) {
    throw "Approved-suite must not hash itself"
}
foreach ($Entry in $LoadedSuite.files) {
    git diff --quiet $PlanCommit -- $Entry.path
    if ($LASTEXITCODE -ne 0) {
        throw "Working plan differs from approved blob: $($Entry.path)"
    }
    $ActualBlob = (git rev-parse "$PlanCommit`:$($Entry.path)").Trim()
    if ($ActualBlob -ne $Entry.git_blob) {
        throw "Plan Git-blob drift: $($Entry.path)"
    }
    $Measured = (& $Python312 -c "import hashlib,subprocess,sys; b=subprocess.check_output(['git','show',sys.argv[1]+':'+sys.argv[2]]); print(hashlib.sha256(b).hexdigest().upper(),len(b))" $PlanCommit $Entry.path).Trim().Split(' ')
    if (
        $LASTEXITCODE -ne 0 -or
        $Measured.Count -ne 2 -or
        [long]$Measured[1] -ne [long]$Entry.bytes -or
        $Measured[0] -cne $Entry.sha256
    ) {
        throw "Plan committed-byte drift: $($Entry.path)"
    }
}
```

Expected: exit code `0`; six unique paths, exact bytes/SHA-256/blob IDs, and
the one shared `plan_commit`.

- [ ] **Step 6: Commit only the approved-suite manifest**

```powershell
$SuiteRelativePath = (
    'docs/superpowers/plans/' +
    '2026-07-30-febio-cae-harness-phase1-approved-suite.json'
)
$BeforeSuiteCommit = (git rev-parse HEAD).Trim()
$SuiteStatus = @(git status --porcelain=v1 --untracked-files=all)
if (
    $BeforeSuiteCommit -cne $PlanCommit -or
    $SuiteStatus.Count -ne 1 -or
    $SuiteStatus[0] -cne "?? $SuiteRelativePath"
) {
    throw 'APPROVED_SUITE_PRECOMMIT_SCOPE_MISMATCH'
}
git add -- $SuiteRelativePath
if ($LASTEXITCODE -ne 0) {
    throw 'APPROVED_SUITE_STAGE_FAILED'
}
git diff --cached --check
if ($LASTEXITCODE -ne 0) {
    throw 'APPROVED_SUITE_DIFF_CHECK_FAILED'
}
$SuiteStagedPaths = @(git diff --cached --name-only)
if (
    $SuiteStagedPaths.Count -ne 1 -or
    $SuiteStagedPaths[0] -cne $SuiteRelativePath
) {
    throw 'APPROVED_SUITE_STAGED_SCOPE_MISMATCH'
}
git commit -m "docs: pin approved phase1 plan suite"
if ($LASTEXITCODE -ne 0) {
    throw 'APPROVED_SUITE_COMMIT_FAILED'
}
$ApprovedSuiteCommit = (git rev-parse HEAD).Trim()
$ApprovedSuiteCommitPaths = @(
    git diff-tree --no-commit-id --name-only -r $ApprovedSuiteCommit
)
if (
    (git rev-parse "$ApprovedSuiteCommit^").Trim() -cne $PlanCommit -or
    $ApprovedSuiteCommitPaths.Count -ne 1 -or
    $ApprovedSuiteCommitPaths[0] -cne $SuiteRelativePath -or
    (git status --porcelain=v1 --untracked-files=all)
) {
    throw 'APPROVED_SUITE_COMMIT_CONTRACT_MISMATCH'
}
```

Expected: one commit and clean `git status --short`.

- [ ] **Step 7: Verify and export the two-commit approved-suite contract**

```powershell
$ApprovedSuiteCommit = (git rev-parse HEAD).Trim()
$ApprovedSuiteParent = (git rev-parse "$ApprovedSuiteCommit^").Trim()
if ($ApprovedSuiteParent -ne $PlanCommit) {
    throw "Approved-suite commit is not the direct child of plan_commit"
}
git cat-file -e "$ApprovedSuiteCommit`:$SuiteRelativePath"
if ($LASTEXITCODE -ne 0) {
    throw "Approved-suite manifest is absent from ApprovedSuiteCommit"
}
$CommittedSuiteBlob = (
    git rev-parse "$ApprovedSuiteCommit`:$SuiteRelativePath"
).Trim()
if ([string]::IsNullOrWhiteSpace($CommittedSuiteBlob)) {
    throw "Approved-suite Git blob is blank"
}
$env:FEBIO_PLAN_COMMIT = $PlanCommit
$env:FEBIO_APPROVED_SUITE_COMMIT = $ApprovedSuiteCommit
```

Expected: `ApprovedSuiteCommit` is exactly one commit after `PlanCommit`;
the former contains the manifest, while `manifest.plan_commit` and all six
`files[*].git_blob` entries bind content from the latter.

- [ ] **Step 7A: Generate a hash-only repository-boundary review candidate**

The committed Phase 1E acceptance specification necessarily contains exact
BottomFrame paths, identifiers, and artifact hashes. They are approved plan
text, not package data. Bind every existing content occurrence to the plan
commit by identifier hash, repository-path SHA-256, one-based per-file
ordinal, and full-line SHA-256. Bind every existing identifier-bearing
repository path separately by identifier hash and exact path SHA-256. This is
the only source of historical exceptions used by Phase 1D. The reviewed policy
may allow those exact committed lines and exact historical paths in the
repository, but never in a wheel, sdist, Skill archive, changed line,
additional occurrence, or different path.

Run this in the same PowerShell session as Steps 1–7:

```powershell
$KnownRealIdentifiers = @(
    'BottomFrame',
    '0729C_CAE_local040_cylD2_L4_aligned_fixed0p1mm',
    '0729C_CAE_local040_cylD2_L4_2p0mm',
    '2026-07-30_0729C_local040-screw',
    'codex/local040-rigid-screw-cylinder',
    'V2.3_Assy,CAE_validation',
    'ABS_Terluran_GP22_23C_QS_N-mm',
    'M2_Screw_Rigid',
    'M2_Screw_Indenter',
    '6351FC75C98BA4830AFC020B1A381A7755775F2BD4C65823477DD9E216070049',
    'CFAA4F106108DA1D2708D7D6EFE01390FA8E882DA5DF99F0B22028A63631A28F',
    '1FC4755DD8D82BEF322D752B57D588FFA0B930512938CF1B585F9EA59A55481B',
    'BD9444F72231B8591D9A1C17AFAD41C384B2767EAA778B1EA19933E40ABEFC7C',
    'B0BE9C4DB6032B2783C23BBB102CAA8DC9D6CFAC9C9FD86C2E3B7A8CF4112A9C',
    'D1C9D46B3A662C92C1BD3ACFA30FAFE948E31C28C860557DCB31A511BC0ED85A',
    '1A66AED65C485F3CB6CFE400FB327E7D28D17E0FA8726593094FB3F7B032DF60',
    '38EA190F17ABAED8061CF2EB065C5611DA907DBB2EF98AE3978FDCF10C70FED6',
    '4859FBEAC85C365F76023B3DB1D47D367185B49502E70B697ED1664783002A40',
    '3c0e672313962e4d14b4d541d27ae8245488b75d'
)
$SetupGateRoot = Join-Path $env:LOCALAPPDATA (
    'FEBioCaeHarness\setup-gate\' + $PlanCommit
)
if (Test-Path -LiteralPath $SetupGateRoot) {
    throw "SETUP_GATE_REVIEW_DIRECTORY_EXISTS: $SetupGateRoot"
}
[void](New-Item -ItemType Directory -Path $SetupGateRoot)
$PendingBaseline = Join-Path $SetupGateRoot 'pending-baseline.json'
$PreviewPath = Join-Path $SetupGateRoot 'cleartext-review-preview.json'
$env:FEBIO_SETUP_REPO = $ToolRoot
$env:FEBIO_SETUP_PLAN_COMMIT = $PlanCommit
$env:FEBIO_SETUP_IDENTIFIERS = (
    $KnownRealIdentifiers | ConvertTo-Json -Compress
)
$env:FEBIO_SETUP_PENDING = $PendingBaseline
$env:FEBIO_SETUP_PREVIEW = $PreviewPath
$SetupGateInventoryScript = @'
import hashlib
import json
import os
from pathlib import Path
import subprocess


def git_bytes(repo: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *arguments])


def normalized(value: str) -> str:
    return "".join(
        character.casefold() for character in value if character.isalnum()
    )


def path_identity(relative: str) -> dict[str, object]:
    encoded = relative.encode("utf-8")
    return {
        "path_utf8_bytes": len(encoded),
        "path_sha256": hashlib.sha256(encoded).hexdigest().upper(),
    }


repo = Path(os.environ["FEBIO_SETUP_REPO"]).resolve(strict=True)
commit = os.environ["FEBIO_SETUP_PLAN_COMMIT"]
identifiers = json.loads(os.environ["FEBIO_SETUP_IDENTIFIERS"])
if not isinstance(identifiers, list) or not identifiers:
    raise SystemExit("IDENTIFIER_REVIEW_SET_EMPTY")
normalized_values = [normalized(str(value)) for value in identifiers]
if any(not value for value in normalized_values):
    raise SystemExit("IDENTIFIER_NORMALIZES_EMPTY")
if len(set(normalized_values)) != len(normalized_values):
    raise SystemExit("IDENTIFIER_NORMALIZATION_COLLISION")
rules = [
    {
        "normalized_length": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest().upper(),
    }
    for value in normalized_values
]
paths = sorted(
    item.decode("utf-8")
    for item in git_bytes(
        repo, "ls-tree", "-r", "--name-only", "-z", commit
    ).split(b"\0")
    if item
)
tracked_files = []
payloads = {}
for relative in paths:
    payload = git_bytes(repo, "show", f"{commit}:{relative}")
    payloads[relative] = payload
    tracked_files.append(
        {
            **path_identity(relative),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest().upper(),
        }
    )
historical = []
historical_paths = []
preview = []
for relative in paths:
    path_record = path_identity(relative)
    for identifier, rule in zip(identifiers, rules, strict=True):
        if normalized(str(identifier)) in normalized(relative):
            historical_paths.append(
                {
                    "identifier_sha256": str(rule["sha256"]),
                    **path_record,
                }
            )
            preview.append(
                {
                    "location_kind": "path",
                    "identifier": identifier,
                    "path": relative,
                    "path_sha256": path_record["path_sha256"],
                }
            )
    ordinals = {rule["sha256"]: 0 for rule in rules}
    for line_number, raw_line in enumerate(
        payloads[relative].splitlines(keepends=True),
        start=1,
    ):
        line = raw_line.decode("utf-8", errors="replace")
        normalized_line = normalized(line)
        line_sha256 = hashlib.sha256(raw_line).hexdigest().upper()
        for identifier, normalized_identifier, rule in zip(
            identifiers, normalized_values, rules, strict=True
        ):
            if normalized_identifier not in normalized_line:
                continue
            identifier_sha256 = str(rule["sha256"])
            ordinals[identifier_sha256] += 1
            historical.append(
                {
                    "identifier_sha256": identifier_sha256,
                    **path_record,
                    "ordinal": ordinals[identifier_sha256],
                    "line_sha256": line_sha256,
                }
            )
            preview.append(
                {
                    "location_kind": "content",
                    "identifier": identifier,
                    "path": relative,
                    "ordinal": ordinals[identifier_sha256],
                    "line_number": line_number,
                    "line_sha256": line_sha256,
                    "line": line,
                }
            )
synthetic_paths = (
    "apps/febio_gmsh_launcher/tests/fixtures/reference_tet4.feb",
    "apps/febio_gmsh_launcher/tests/fixtures/unsupported_node_selection.feb",
)
synthetic = []
for relative in synthetic_paths:
    if relative not in payloads:
        raise SystemExit(f"REVIEWED_SYNTHETIC_FIXTURE_MISSING:{relative}")
    payload = payloads[relative]
    synthetic.append(
        {
            **path_identity(relative),
            "max_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest().upper(),
        }
    )
    preview.append(
        {
            "location_kind": "synthetic_fixture",
            "path": relative,
            **path_identity(relative),
            "file_bytes": len(payload),
            "file_sha256": hashlib.sha256(
                payload
            ).hexdigest().upper(),
        }
    )
baseline = {
    "schema_version": 1,
    "base_commit": commit,
    "tracked_files": sorted(
        tracked_files, key=lambda item: item["path_sha256"]
    ),
    "forbidden_identifiers": sorted(
        rules, key=lambda item: (item["sha256"], item["normalized_length"])
    ),
    "approved_historical_references": sorted(
        historical,
        key=lambda item: (
            item["path_sha256"],
            item["identifier_sha256"],
            item["ordinal"],
        ),
    ),
    "approved_historical_paths": sorted(
        historical_paths,
        key=lambda item: (
            item["path_sha256"],
            item["identifier_sha256"],
        ),
    ),
    "approved_synthetic_fixtures": sorted(
        synthetic, key=lambda item: item["path_sha256"]
    ),
}
pending = Path(os.environ["FEBIO_SETUP_PENDING"])
preview_path = Path(os.environ["FEBIO_SETUP_PREVIEW"])
pending.write_text(
    json.dumps(
        baseline, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    + "\n",
    encoding="utf-8",
    newline="\n",
)
preview_path.write_text(
    json.dumps(
        preview, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    + "\n",
    encoding="utf-8",
    newline="\n",
)
print(
    json.dumps(
        {
            "base_commit": commit,
            "identifier_rule_count": len(rules),
            "historical_reference_count": len(historical),
            "historical_path_count": len(historical_paths),
            "tracked_file_count": len(tracked_files),
            "pending_baseline": str(pending),
            "pending_sha256": hashlib.sha256(
                pending.read_bytes()
            ).hexdigest().upper(),
            "cleartext_preview": str(preview_path),
            "cleartext_preview_sha256": hashlib.sha256(
                preview_path.read_bytes()
            ).hexdigest().upper(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
)
'@
& $Python312 -c $SetupGateInventoryScript
if ($LASTEXITCODE -ne 0) {
    throw 'SETUP_GATE_BOUNDARY_CANDIDATE_FAILED'
}
$PendingSha256 = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $PendingBaseline
).Hash
$PreviewSha256 = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $PreviewPath
).Hash
Get-Content -LiteralPath $PreviewPath -Encoding UTF8 -Raw |
    ConvertFrom-Json |
    Format-List location_kind,identifier,path,line_number,ordinal,line_sha256,path_sha256,line,file_bytes,file_sha256
Write-Output (
    "REVIEW_REQUIRED plan_commit=$PlanCommit " +
    "approved_suite_commit=$ApprovedSuiteCommit " +
    "pending_sha256=$PendingSha256 preview_sha256=$PreviewSha256"
)
```

Expected: one candidate with all tracked plan-commit files, 19 unique
hash-only identifier rules, both pre-existing synthetic FEB fixtures, and every
matched line shown for human review. The candidate and clear-text preview are
outside Git. Do not continue in this turn. Ask the human to inspect the preview
and reply exactly:

```text
APPROVE REPOSITORY BASELINE <full plan_commit> <full approved-suite commit> <pending-baseline SHA-256> <cleartext-preview SHA-256>
```

- [ ] **Step 7B: Promote and commit only the explicitly approved hash binding**

After receiving that exact response, start from this block; do not rerun Step 1,
because the approved branch and worktree must now exist. Set
`FEBIO_SETUP_HUMAN_RESPONSE` to the verbatim subsequent human response; the
executing agent must not create, complete, normalize, or infer that text. The
following block mechanically compares all four bound tokens before it promotes
anything.

```powershell
$ToolRoot = 'C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\febio-tools'
$Worktree = 'C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\_worktrees\febio-cae-harness-phase1'
$Branch = 'codex/febio-cae-harness-phase1'
$Python312 = 'C:\Users\backo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$PlanSuite = @(
    'docs/superpowers/plans/2026-07-30-febio-llm-cae-harness-phase1.md',
    'docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1a-core.md',
    'docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1b-runner-evidence.md',
    'docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1c-orchestration-report.md',
    'docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1d-codex-release.md',
    'docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1e-real-model-e2e.md'
)
$SuiteRelativePath = (
    'docs/superpowers/plans/' +
    '2026-07-30-febio-cae-harness-phase1-approved-suite.json'
)
$BoundaryReviewRelative = (
    'docs/superpowers/plans/' +
    '2026-07-30-febio-cae-harness-phase1-boundary-review.json'
)
if (-not (Test-Path -LiteralPath $Worktree -PathType Container)) {
    throw 'APPROVED_WORKTREE_MISSING'
}
Set-Location -LiteralPath $Worktree
$ActualRoot = [IO.Path]::GetFullPath(
    (git rev-parse --show-toplevel).Trim()
)
$ExpectedWorktreeRoot = [IO.Path]::GetFullPath($Worktree)
$ActualBranch = (git branch --show-current).Trim()
$ApprovedSuiteCommit = (git rev-parse HEAD).Trim()
$PlanCommit = (git rev-parse "$ApprovedSuiteCommit^").Trim()
if (
    $ActualRoot -cne $ExpectedWorktreeRoot -or
    $ActualBranch -cne $Branch -or
    [string]::IsNullOrWhiteSpace($PlanCommit)
) {
    throw 'APPROVED_WORKTREE_IDENTITY_MISMATCH'
}
$ApprovedSuitePaths = @(
    git diff-tree --no-commit-id --name-only -r $ApprovedSuiteCommit
)
if (
    $ApprovedSuitePaths.Count -ne 1 -or
    $ApprovedSuitePaths[0] -cne $SuiteRelativePath
) {
    throw 'APPROVED_SUITE_COMMIT_SCOPE_MISMATCH'
}
$CommittedSuite = (
    git show "$ApprovedSuiteCommit`:$SuiteRelativePath"
) | ConvertFrom-Json
if (
    $CommittedSuite.schema_version -ne 1 -or
    $CommittedSuite.plan_commit -cne $PlanCommit -or
    @($CommittedSuite.files).Count -ne 6 -or
    @($CommittedSuite.files.path | Sort-Object -Unique).Count -ne 6 -or
    (Compare-Object @($PlanSuite | Sort-Object) @(
        $CommittedSuite.files.path | Sort-Object
    ))
) {
    throw 'APPROVED_SUITE_MANIFEST_BINDING_MISMATCH'
}
foreach ($Entry in $CommittedSuite.files) {
    $CommittedBlob = (
        git rev-parse "$PlanCommit`:$($Entry.path)"
    ).Trim()
    git diff --quiet $PlanCommit -- $Entry.path
    if ($LASTEXITCODE -ne 0) {
        throw "APPROVED_PLAN_WORKTREE_DRIFT: $($Entry.path)"
    }
    $Measured = (& $Python312 -c "import hashlib,subprocess,sys; b=subprocess.check_output(['git','show',sys.argv[1]+':'+sys.argv[2]]); print(hashlib.sha256(b).hexdigest().upper(),len(b))" $PlanCommit $Entry.path).Trim().Split(' ')
    if (
        $CommittedBlob -cne $Entry.git_blob -or
        $LASTEXITCODE -ne 0 -or
        $Measured.Count -ne 2 -or
        [long]$Measured[1] -ne [long]$Entry.bytes -or
        $Measured[0] -cne $Entry.sha256
    ) {
        throw "APPROVED_PLAN_BINDING_MISMATCH: $($Entry.path)"
    }
}
$BoundaryReviewPath = Join-Path $Worktree $BoundaryReviewRelative
$ResumeStatus = @(git status --porcelain=v1 --untracked-files=all)
if (
    $ResumeStatus.Count -gt 1 -or
    (
        $ResumeStatus.Count -eq 1 -and
        (
            $ResumeStatus[0].Length -lt 4 -or
            $ResumeStatus[0].Substring(3) -cne $BoundaryReviewRelative
        )
    ) -or
    (
        $ResumeStatus.Count -eq 0 -and
        (Test-Path -LiteralPath $BoundaryReviewPath)
    )
) {
    throw 'BOUNDARY_REVIEW_REQUIRES_CLEAN_WORKTREE'
}
$SetupGateRoot = Join-Path $env:LOCALAPPDATA (
    'FEBioCaeHarness\setup-gate\' + $PlanCommit
)
$PendingBaseline = Join-Path $SetupGateRoot 'pending-baseline.json'
$PreviewPath = Join-Path $SetupGateRoot 'cleartext-review-preview.json'
$ReviewedBaseline = Join-Path $SetupGateRoot 'reviewed-baseline.json'
$ReviewRecordPath = Join-Path $SetupGateRoot 'review-record.json'
if (-not (Test-Path -LiteralPath $PendingBaseline -PathType Leaf)) {
    throw 'SETUP_GATE_PENDING_BASELINE_MISSING'
}
if (-not (Test-Path -LiteralPath $PreviewPath -PathType Leaf)) {
    throw 'SETUP_GATE_PREVIEW_MISSING'
}
$PendingSha256 = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $PendingBaseline
).Hash
$PreviewSha256 = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $PreviewPath
).Hash
$HumanResponse = [string]$env:FEBIO_SETUP_HUMAN_RESPONSE
if ([string]::IsNullOrWhiteSpace($HumanResponse)) {
    throw 'SETUP_GATE_HUMAN_RESPONSE_MISSING'
}
$ExpectedResponse = (
    "APPROVE REPOSITORY BASELINE $PlanCommit " +
    "$ApprovedSuiteCommit $PendingSha256 $PreviewSha256"
)
if ($HumanResponse -cne $ExpectedResponse) {
    throw 'SETUP_GATE_HUMAN_RESPONSE_BINDING_MISMATCH'
}
if (Test-Path -LiteralPath $ReviewedBaseline -PathType Leaf) {
    if (
        (Get-FileHash -Algorithm SHA256 -LiteralPath $ReviewedBaseline).Hash -cne
            $PendingSha256
    ) {
        throw 'SETUP_GATE_EXISTING_REVIEWED_BASELINE_MISMATCH'
    }
}
else {
    [System.IO.File]::Copy($PendingBaseline, $ReviewedBaseline, $false)
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $ReviewedBaseline).Hash -ne
    $PendingSha256) {
    throw 'SETUP_GATE_REVIEWED_BASELINE_COPY_MISMATCH'
}
$Sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $ApprovalTextSha256 = [BitConverter]::ToString(
        $Sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($HumanResponse))
    ).Replace('-', '')
}
finally {
    $Sha256.Dispose()
}
$ReviewRecord = [ordered]@{
    schema_version = 1
    plan_commit = $PlanCommit
    approved_suite_commit = $ApprovedSuiteCommit
    reviewed_baseline_sha256 = $PendingSha256
    cleartext_preview_sha256 = $PreviewSha256
    approval_text_sha256 = $ApprovalTextSha256
}
$ReviewRecordJson = $ReviewRecord | ConvertTo-Json -Compress
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$ReviewRecordBytes = $Utf8NoBom.GetBytes($ReviewRecordJson + "`n")
$Sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $ExpectedReviewRecordSha256 = [BitConverter]::ToString(
        $Sha256.ComputeHash($ReviewRecordBytes)
    ).Replace('-', '')
}
finally {
    $Sha256.Dispose()
}
if (Test-Path -LiteralPath $ReviewRecordPath -PathType Leaf) {
    if (
        (Get-FileHash -Algorithm SHA256 -LiteralPath $ReviewRecordPath).Hash -cne
            $ExpectedReviewRecordSha256
    ) {
        throw 'SETUP_GATE_EXISTING_REVIEW_RECORD_MISMATCH'
    }
}
else {
    $ReviewStream = [System.IO.File]::Open(
        $ReviewRecordPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::Read
    )
    try {
        $ReviewStream.Write(
            $ReviewRecordBytes, 0, $ReviewRecordBytes.Length
        )
        $ReviewStream.Flush($true)
    }
    finally {
        $ReviewStream.Dispose()
    }
}
$SetupGateReviewRecordSha256 = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $ReviewRecordPath
).Hash
$BoundaryReview = [ordered]@{
    schema_version = 1
    plan_commit = $PlanCommit
    approved_suite_commit = $ApprovedSuiteCommit
    reviewed_baseline_sha256 = $PendingSha256
    cleartext_preview_sha256 = $PreviewSha256
    approval_text_sha256 = $ApprovalTextSha256
    review_record_sha256 = $SetupGateReviewRecordSha256
}
$BoundaryReviewJson = $BoundaryReview | ConvertTo-Json -Compress
$BoundaryReviewBytes = $Utf8NoBom.GetBytes($BoundaryReviewJson + "`n")
$Sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
    $ExpectedBoundaryReviewSha256 = [BitConverter]::ToString(
        $Sha256.ComputeHash($BoundaryReviewBytes)
    ).Replace('-', '')
}
finally {
    $Sha256.Dispose()
}
if (Test-Path -LiteralPath $BoundaryReviewPath -PathType Leaf) {
    if (
        (Get-FileHash -Algorithm SHA256 -LiteralPath $BoundaryReviewPath).Hash -cne
            $ExpectedBoundaryReviewSha256
    ) {
        throw 'EXISTING_BOUNDARY_REVIEW_MANIFEST_MISMATCH'
    }
}
else {
    $BoundaryStream = [System.IO.File]::Open(
        $BoundaryReviewPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::Read
    )
    try {
        $BoundaryStream.Write(
            $BoundaryReviewBytes, 0, $BoundaryReviewBytes.Length
        )
        $BoundaryStream.Flush($true)
    }
    finally {
        $BoundaryStream.Dispose()
    }
}
git add -- $BoundaryReviewRelative
if ($LASTEXITCODE -ne 0) {
    throw 'BOUNDARY_REVIEW_STAGE_FAILED'
}
git diff --cached --check
if ($LASTEXITCODE -ne 0) {
    throw 'BOUNDARY_REVIEW_DIFF_CHECK_FAILED'
}
$BoundaryStagedPaths = @(git diff --cached --name-only)
if (
    $BoundaryStagedPaths.Count -ne 1 -or
    $BoundaryStagedPaths[0] -cne $BoundaryReviewRelative
) {
    throw 'BOUNDARY_REVIEW_STAGED_SCOPE_MISMATCH'
}
git commit -m "docs: bind approved repository boundary baseline"
if ($LASTEXITCODE -ne 0) {
    throw 'BOUNDARY_REVIEW_COMMIT_FAILED'
}
$BoundaryApprovalCommit = (git rev-parse HEAD).Trim()
if ((git rev-parse "$BoundaryApprovalCommit^").Trim() -ne
    $ApprovedSuiteCommit) {
    throw 'BOUNDARY_APPROVAL_COMMIT_PARENT_MISMATCH'
}
$BoundaryCommitPaths = @(
    git diff-tree --no-commit-id --name-only -r $BoundaryApprovalCommit
)
if (
    $BoundaryCommitPaths.Count -ne 1 -or
    $BoundaryCommitPaths[0] -cne $BoundaryReviewRelative
) {
    throw 'BOUNDARY_APPROVAL_COMMIT_SCOPE_MISMATCH'
}
git diff --quiet $BoundaryApprovalCommit -- $BoundaryReviewRelative
if ($LASTEXITCODE -ne 0) {
    throw 'BOUNDARY_APPROVAL_WORKTREE_DRIFT'
}
$BoundaryReviewManifestSha256 = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $BoundaryReviewPath
).Hash
if (git status --porcelain=v1 --untracked-files=all) {
    throw 'BOUNDARY_APPROVAL_COMMIT_NOT_CLEAN'
}
$SetupGateBaseline = $ReviewedBaseline
$SetupGateReviewRecord = $ReviewRecordPath
$ReviewedBaselineSha256 = $PendingSha256
```

Expected: one create-new `reviewed-baseline.json`, byte-identical to the
human-approved candidate; one external create-new hash-only
`review-record.json`; and one Git commit, directly after
`ApprovedSuiteCommit`, containing only the hash-only boundary-review manifest.
`BoundaryApprovalCommit` is the immutable root consumed by later phases. The
external baseline, preview, and review record are never staged, packaged, or
copied into `02_CAE`.

- [ ] **Step 8: Re-read the worktree policy and verify Python**

```powershell
Get-Content -Raw -LiteralPath .\AGENTS.md
if (-not (Test-Path -LiteralPath $Python312)) {
    throw "Pinned Python is missing: $Python312"
}
& $Python312 --version
```

Expected:

```text
Python 3.12.13
```

- [ ] **Step 9: Create the worktree-local development environment**

```powershell
& $Python312 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --disable-pip-version-check `
    -e apps/febio_gmsh_launcher pytest==9.1.1
if ($LASTEXITCODE -ne 0) {
    throw "Baseline environment installation failed"
}
```

Expected: exit code `0`; `.venv\Scripts\python.exe` exists under `$Worktree`.

- [ ] **Step 10: Establish the unchanged-launcher baseline**

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_gmsh_launcher/tests -q
if ($LASTEXITCODE -ne 0) {
    throw "Existing launcher baseline is not green"
}
git status --short --branch
git worktree list --porcelain
```

Expected: pytest exit code `0`, no failed/error tests, and no tracked worktree change.

---

### Task 1: Scaffold the package, response envelope, and installed schemas

**Files:**

- Create: `apps/febio_cae_harness/pyproject.toml`
- Create: `apps/febio_cae_harness/README.md`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/__init__.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/cli.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/errors.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/response.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schema.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/__init__.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/command-result.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/data/__init__.py`
- Create: `apps/febio_cae_harness/tests/unit/test_response.py`
- Create: `apps/febio_cae_harness/tests/unit/test_cli_base.py`
- Create: `apps/febio_cae_harness/tests/contract/test_command_result_schema.py`
- Create: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**

- Consumes: Python `3.12.x`; `jsonschema==4.26.0`; no `febio_gmsh_launcher` runtime import.
- Produces: lowercase `Status`, `ExitCode`, closed `EvidenceRecord(kind, data)`, `CommandResult`, `validate_schema(name, instance)`, `emit_result(result, stream)`, and `main(argv)`.

- [ ] **Step 1: Write the response and schema tests**

Create `tests/unit/test_response.py`:

```python
import io
import json

import pytest

from febio_cae_harness.errors import ExitCode, Status
from febio_cae_harness.response import CommandResult, EvidenceRecord, emit_result
from febio_cae_harness.schema import validate_schema


def test_success_result_is_one_schema_valid_json_line() -> None:
    stream = io.StringIO()
    result = CommandResult(
        exit_code=ExitCode.SUCCESS,
        status=Status.SUCCESS,
        case_state="CASE_CREATED",
        evidence=(EvidenceRecord("case_initialized", {"analysis_id": "a-001"}),),
    )

    exit_code = emit_result(result, stream)

    assert exit_code is ExitCode.SUCCESS
    assert stream.getvalue().endswith("\n")
    assert stream.getvalue().count("\n") == 1
    payload = json.loads(stream.getvalue())
    validate_schema("command-result", payload)
    assert payload["schema_version"] == 1
    assert payload["exit_code"] == 0
    assert payload["status"] == "success"
    assert payload["evidence"] == [
        {"kind": "case_initialized", "data": {"analysis_id": "a-001"}}
    ]
    assert payload["error"] is None


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [
        (Status.SUCCESS, ExitCode.WAITING_FOR_HUMAN),
        (Status.WAITING_FOR_HUMAN, ExitCode.SUCCESS),
        (Status.ERROR, ExitCode.SUCCESS),
    ],
)
def test_status_exit_code_mismatch_is_rejected(
    status: Status,
    exit_code: ExitCode,
) -> None:
    with pytest.raises(ValueError, match="status/exit-code mismatch"):
        CommandResult(exit_code=exit_code, status=status, case_state=None)
```

Create `tests/contract/test_command_result_schema.py`:

```python
import pytest
from jsonschema import ValidationError

from febio_cae_harness.schema import validate_schema


def valid_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "exit_code": 0,
        "status": "success",
        "case_state": "CASE_CREATED",
        "blockers": [],
        "allowed_next_actions": [],
        "artifacts": [],
        "evidence": [],
        "error": None,
    }


def test_schema_rejects_extra_property() -> None:
    payload = valid_payload()
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        validate_schema("command-result", payload)


def test_schema_rejects_invalid_exit_code() -> None:
    payload = valid_payload()
    payload["exit_code"] = 99
    with pytest.raises(ValidationError):
        validate_schema("command-result", payload)
```

- [ ] **Step 2: Write the subprocess and resource tests**

Create `tests/unit/test_cli_base.py`:

```python
import json
import subprocess
import sys


def run_cli(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "febio_cae_harness.cli", *args],
        capture_output=True,
        check=False,
        timeout=10,
    )


def assert_single_utf8_json(stdout: bytes) -> dict[str, object]:
    text = stdout.decode("utf-8", errors="strict")
    assert text.count("\n") == 1
    return json.loads(text)


def test_version_is_json_only() -> None:
    completed = run_cli("--version")
    assert completed.returncode == 0
    payload = assert_single_utf8_json(completed.stdout)
    assert payload["status"] == "success"
    assert payload["evidence"] == [
        {"kind": "package_version", "data": {"version": "0.1.0"}}
    ]


def test_missing_command_is_json_only() -> None:
    completed = run_cli()
    assert completed.returncode == 30
    payload = assert_single_utf8_json(completed.stdout)
    assert payload["error"] == {
        "code": "INVALID_COMMAND",
        "message": "a command is required",
    }
```

Create `tests/contract/test_installed_resources.py`:

```python
from importlib.resources import files


EXPECTED_RESOURCES = {
    "schemas/command-result.schema.json",
}


def test_resource_inventory() -> None:
    package = files("febio_cae_harness")
    missing = [
        resource
        for resource in sorted(EXPECTED_RESOURCES)
        if not package.joinpath(resource).is_file()
    ]
    assert missing == []
```

- [ ] **Step 3: Run the tests to verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_response.py `
  apps/febio_cae_harness/tests/unit/test_cli_base.py `
  apps/febio_cae_harness/tests/contract/test_command_result_schema.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: collection fails with:

```text
ModuleNotFoundError: No module named 'febio_cae_harness'
```

- [ ] **Step 4: Add package metadata and package markers**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools==83.0.0"]
build-backend = "setuptools.build_meta"

[project]
name = "febio-cae-harness"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "jsonschema==4.26.0",
  "psutil==7.2.2",
]

[project.optional-dependencies]
dev = ["build==1.5.0", "pytest==9.1.1"]
step = ["gmsh==4.15.2"]

[project.scripts]
febio-cae = "febio_cae_harness.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
febio_cae_harness = [
  "schemas/*.json",
  "templates/*.html",
  "data/*.json",
  "data/policies/*.json",
]

[tool.pytest.ini_options]
pythonpath = ["src", "tests/contract"]
testpaths = ["tests"]
markers = [
  "real_solver: invokes installed FEBio",
  "real_fbs: invokes official FBS bridge",
  "windows_job: requires Windows Job Objects",
]
```

Create `src/febio_cae_harness/__init__.py`:

```python
__version__ = "0.1.0"
```

Create empty package markers:

```python
# src/febio_cae_harness/schemas/__init__.py
```

```python
# src/febio_cae_harness/data/__init__.py
```

- [ ] **Step 5: Implement exit codes and response emission**

Create `src/febio_cae_harness/errors.py`:

```python
from enum import IntEnum, StrEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    WAITING_FOR_HUMAN = 10
    INVALID_INPUT_OR_CONTRACT = 20
    INVALID_STATE_OR_POLICY = 30
    RESOURCE_OR_TOOL_ERROR = 40
    SOLVE_FAILED = 50
    RESULT_INCOMPLETE = 60
    INTERNAL_ERROR = 70


class Status(StrEnum):
    SUCCESS = "success"
    WAITING_FOR_HUMAN = "waiting_for_human"
    ERROR = "error"


class PolicyViolation(ValueError):
    """A deterministic workspace or immutable-write policy failure."""
```

Create `src/febio_cae_harness/response.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TextIO

from .errors import ExitCode, Status
from .schema import validate_schema


@dataclass(frozen=True)
class EvidenceRecord:
    kind: str
    data: dict[str, object]

    def __post_init__(self) -> None:
        if not self.kind or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in self.kind
        ):
            raise ValueError("evidence kind must be lowercase snake/kebab case")

    def to_payload(self) -> dict[str, object]:
        return {"kind": self.kind, "data": self.data}


@dataclass(frozen=True)
class CommandResult:
    exit_code: ExitCode
    status: Status
    case_state: str | None
    blockers: tuple[dict[str, object], ...] = ()
    allowed_next_actions: tuple[str, ...] = ()
    artifacts: tuple[dict[str, object], ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    error: dict[str, object] | None = None

    def __post_init__(self) -> None:
        valid = (
            (self.status is Status.SUCCESS and self.exit_code is ExitCode.SUCCESS)
            or (
                self.status is Status.WAITING_FOR_HUMAN
                and self.exit_code is ExitCode.WAITING_FOR_HUMAN
            )
            or (
                self.status is Status.ERROR
                and self.exit_code
                not in {ExitCode.SUCCESS, ExitCode.WAITING_FOR_HUMAN}
            )
        )
        if not valid:
            raise ValueError("status/exit-code mismatch")

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "exit_code": int(self.exit_code),
            "status": self.status.value,
            "case_state": self.case_state,
            "blockers": list(self.blockers),
            "allowed_next_actions": list(self.allowed_next_actions),
            "artifacts": list(self.artifacts),
            "evidence": [item.to_payload() for item in self.evidence],
            "error": self.error,
        }


def emit_result(result: CommandResult, stream: TextIO) -> ExitCode:
    payload = result.to_payload()
    validate_schema("command-result", payload)
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    stream.write(serialized + "\n")
    stream.flush()
    return result.exit_code
```

- [ ] **Step 6: Implement schema loading and the minimal CLI**

Create `src/febio_cae_harness/schema.py`:

```python
from __future__ import annotations

import json
from importlib.resources import files

from jsonschema import Draft202012Validator


def load_schema(name: str) -> dict[str, object]:
    resource = files("febio_cae_harness.schemas").joinpath(f"{name}.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def validate_schema(name: str, instance: object) -> None:
    Draft202012Validator(load_schema(name)).validate(instance)
```

Create `src/febio_cae_harness/cli.py`:

```python
from __future__ import annotations

import sys
from collections.abc import Sequence

from . import __version__
from .errors import ExitCode, Status
from .response import CommandResult, EvidenceRecord, emit_result


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict", newline="\n")
    if args == ["--version"]:
        result = CommandResult(
            exit_code=ExitCode.SUCCESS,
            status=Status.SUCCESS,
            case_state=None,
            evidence=(
                EvidenceRecord(
                    kind="package_version",
                    data={"version": __version__},
                ),
            ),
        )
    else:
        result = CommandResult(
            exit_code=ExitCode.INVALID_STATE_OR_POLICY,
            status=Status.ERROR,
            case_state=None,
            error={
                "code": "INVALID_COMMAND",
                "message": "a command is required",
            },
        )
    return int(emit_result(result, sys.stdout))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Add the command-result schema and trust-boundary README**

Create `src/febio_cae_harness/schemas/command-result.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "urn:febio-cae-harness:command-result:1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "exit_code",
    "status",
    "case_state",
    "blockers",
    "allowed_next_actions",
    "artifacts",
    "evidence",
    "error"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "exit_code": {"enum": [0, 10, 20, 30, 40, 50, 60, 70]},
    "status": {"enum": ["success", "waiting_for_human", "error"]},
    "case_state": {"type": ["string", "null"]},
    "blockers": {"type": "array", "items": {"type": "object"}},
    "allowed_next_actions": {"type": "array", "items": {"type": "string"}},
    "artifacts": {"type": "array", "items": {"type": "object"}},
    "evidence": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["kind", "data"],
        "properties": {
          "kind": {
            "type": "string",
            "pattern": "^[a-z0-9]+(?:[_-][a-z0-9]+)*$"
          },
          "data": {"type": "object"}
        }
      }
    },
    "error": {"type": ["object", "null"]}
  }
}
```

Create `README.md`:

```markdown
# FEBio CAE Harness

The harness manages audited FEBio CAE cases. Phase 1A implements case state,
immutable input provenance, read-only inspection, intent revision, and approval
binding. It does not run FEBio.

The CLI verifies the recorded approval text, nonce, contract, inputs, source
selection, and execution-profile hashes. It cannot cryptographically prove that
the speaker was human. Codex must never invent or replay approval text.
```

- [ ] **Step 8: Install editable and verify GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pip install --disable-pip-version-check `
  -e 'apps/febio_cae_harness[dev]'
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_response.py `
  apps/febio_cae_harness/tests/unit/test_cli_base.py `
  apps/febio_cae_harness/tests/contract/test_command_result_schema.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: `PASS` with exit code `0` and no failed/error tests.

- [ ] **Step 9: Commit the package contract**

```powershell
git status --short
git add `
  apps/febio_cae_harness/pyproject.toml `
  apps/febio_cae_harness/README.md `
  apps/febio_cae_harness/src/febio_cae_harness/__init__.py `
  apps/febio_cae_harness/src/febio_cae_harness/cli.py `
  apps/febio_cae_harness/src/febio_cae_harness/errors.py `
  apps/febio_cae_harness/src/febio_cae_harness/response.py `
  apps/febio_cae_harness/src/febio_cae_harness/schema.py `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/__init__.py `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/command-result.schema.json `
  apps/febio_cae_harness/src/febio_cae_harness/data/__init__.py `
  apps/febio_cae_harness/tests/unit/test_response.py `
  apps/febio_cae_harness/tests/unit/test_cli_base.py `
  apps/febio_cae_harness/tests/contract/test_command_result_schema.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: scaffold FEBio CAE harness contracts"
```

Expected: one commit; `git status --short` is empty afterward.

## Task 3: Enforce the CAE workspace boundary

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/workspace.py`
- Create: `apps/febio_cae_harness/tests/unit/test_workspace.py`

**Interfaces:**

- Consumes: absolute `cae_root`, `tool_root`, candidate case paths, attempt IDs, and `PolicyViolation` from `errors.py`.
- Produces: `WorkspacePolicy.validate_case_root()`, `WorkspacePolicy.attempt_root()`, and `WorkspacePolicy.validate_write_target()` returning resolved `Path` values or failing closed with `WORKSPACE_POLICY`.

- [ ] **Step 1: Add boundary tests**

Create `apps/febio_cae_harness/tests/unit/test_workspace.py`:

```python
import json
from pathlib import Path

import pytest

from febio_cae_harness.errors import PolicyViolation
from febio_cae_harness.workspace import WorkspacePolicy


def make_policy(tmp_path: Path) -> tuple[WorkspacePolicy, Path, Path]:
    workspace = tmp_path / "FEBio"
    cae_root = workspace / "02_CAE"
    tool_root = workspace / "01_Tools" / "febio-tools"
    cae_root.mkdir(parents=True)
    tool_root.mkdir(parents=True)
    return WorkspacePolicy(cae_root, tool_root), cae_root, tool_root


def test_accepts_absolute_case_below_02_cae(tmp_path: Path) -> None:
    policy, cae_root, _ = make_policy(tmp_path)
    case = cae_root / "2026-07-30" / "bracket"
    case.mkdir(parents=True)
    assert policy.validate_case_root(case) == case.resolve()


@pytest.mark.parametrize("kind", ["relative", "cae-root", "tool-root", "outside"])
def test_rejects_paths_outside_case_boundary(tmp_path: Path, kind: str) -> None:
    policy, cae_root, tool_root = make_policy(tmp_path)
    candidates = {
        "relative": Path("02_CAE/case"),
        "cae-root": cae_root,
        "tool-root": tool_root,
        "outside": tmp_path / "elsewhere",
    }
    candidates["outside"].mkdir(exist_ok=True)
    with pytest.raises(PolicyViolation, match="WORKSPACE_POLICY"):
        policy.validate_case_root(candidates[kind])


def test_rejects_git_marker_below_cae_root(tmp_path: Path) -> None:
    policy, cae_root, _ = make_policy(tmp_path)
    parent = cae_root / "project"
    case = parent / "case"
    case.mkdir(parents=True)
    (parent / ".git").write_text("gitdir: forbidden\n", encoding="utf-8")
    with pytest.raises(PolicyViolation, match="Git metadata"):
        policy.validate_case_root(case)


def test_rejects_reparse_component(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    policy, cae_root, _ = make_policy(tmp_path)
    case = cae_root / "linked" / "case"
    case.mkdir(parents=True)
    monkeypatch.setattr(policy, "_is_reparse_point", lambda path: path.name == "linked")
    with pytest.raises(PolicyViolation, match="reparse"):
        policy.validate_case_root(case)


def test_attempt_output_is_confined_to_90_temporary(tmp_path: Path) -> None:
    policy, cae_root, _ = make_policy(tmp_path)
    case = cae_root / "case"
    case.mkdir()
    attempt = policy.attempt_root(case, "attempt-0001")
    attempt.mkdir(parents=True)
    assert attempt == (case / "90_Temporary" / "attempt-0001").resolve()
    assert policy.validate_write_target(case, attempt / "model.feb") == (
        attempt / "model.feb"
    ).resolve()
    with pytest.raises(PolicyViolation, match="90_Temporary"):
        policy.validate_write_target(case, case / "02_Model" / "model.feb")


@pytest.mark.parametrize("attempt_id", ["", ".", "..", "a/b", r"a\b", "a space"])
def test_rejects_unsafe_attempt_id(
    tmp_path: Path,
    attempt_id: str,
) -> None:
    policy, cae_root, _ = make_policy(tmp_path)
    case = cae_root / "case"
    case.mkdir()
    with pytest.raises(PolicyViolation, match="attempt_id"):
        policy.attempt_root(case, attempt_id)
```

- [ ] **Step 2: Prove RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_workspace.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.workspace'`.

- [ ] **Step 3: Implement the workspace policy**

Create `apps/febio_cae_harness/src/febio_cae_harness/workspace.py`:

```python
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import PolicyViolation

_ATTEMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_REPARSE_POINT = 0x0400


def _is_within(child: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((str(child), str(parent))) == str(parent)
    except ValueError:
        return False


@dataclass(frozen=True)
class WorkspacePolicy:
    cae_root: Path
    tool_root: Path

    def __post_init__(self) -> None:
        if not self.cae_root.is_absolute() or not self.tool_root.is_absolute():
            raise PolicyViolation("WORKSPACE_POLICY: roots must be absolute")
        object.__setattr__(self, "cae_root", self.cae_root.resolve(strict=True))
        object.__setattr__(self, "tool_root", self.tool_root.resolve(strict=True))

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attributes & _REPARSE_POINT)

    def _reject_reparse_chain(self, path: Path) -> None:
        cursor = path if path.exists() else path.parent
        while _is_within(cursor, self.cae_root):
            if self._is_reparse_point(cursor):
                raise PolicyViolation(
                    f"WORKSPACE_POLICY: reparse component is forbidden: {cursor}"
                )
            if cursor == self.cae_root:
                break
            cursor = cursor.parent

    def validate_case_root(self, case_dir: Path) -> Path:
        if not case_dir.is_absolute():
            raise PolicyViolation("WORKSPACE_POLICY: case path must be absolute")
        resolved = case_dir.resolve(strict=True)
        if resolved == self.cae_root or not _is_within(resolved, self.cae_root):
            raise PolicyViolation("WORKSPACE_POLICY: case must be below 02_CAE")
        if resolved == self.tool_root or _is_within(resolved, self.tool_root):
            raise PolicyViolation("WORKSPACE_POLICY: CAE output cannot enter tool root")
        self._reject_reparse_chain(case_dir)
        cursor = resolved
        while cursor != self.cae_root:
            if (cursor / ".git").exists():
                raise PolicyViolation(
                    f"WORKSPACE_POLICY: Git metadata below 02_CAE: {cursor}"
                )
            cursor = cursor.parent
        return resolved

    def attempt_root(self, case_dir: Path, attempt_id: str) -> Path:
        case = self.validate_case_root(case_dir)
        if not _ATTEMPT_ID.fullmatch(attempt_id) or attempt_id in {".", ".."}:
            raise PolicyViolation("WORKSPACE_POLICY: unsafe attempt_id")
        return (case / "90_Temporary" / attempt_id).resolve()

    def validate_write_target(self, case_dir: Path, target: Path) -> Path:
        case = self.validate_case_root(case_dir)
        temporary = (case / "90_Temporary").resolve()
        resolved = target.resolve(strict=False)
        if resolved == temporary or not _is_within(resolved, temporary):
            raise PolicyViolation(
                "WORKSPACE_POLICY: generated output must stay in 90_Temporary"
            )
        self._reject_reparse_chain(target)
        return resolved
```

- [ ] **Step 4: Verify focused GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_workspace.py -q
```

Expected: `PASS` with exit code `0`.

- [ ] **Step 5: Commit the workspace boundary**

```powershell
git add `
  apps/febio_cae_harness/src/febio_cae_harness/workspace.py `
  apps/febio_cae_harness/tests/unit/test_workspace.py
git commit -m "feat: enforce CAE workspace policy"
```

Expected: one commit and a clean `git status --short`.

## Task 10: Inspect FEB input read-only and compute the v1 domain signature

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/feb_inspector.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/feb-inspection.schema.json`
- Create: `apps/febio_cae_harness/tests/fixtures/feb/complete-small.feb`
- Create: `apps/febio_cae_harness/tests/fixtures/feb/missing-surface.feb`
- Create: `apps/febio_cae_harness/tests/unit/test_feb_inspector.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**

- Consumes: a read-only FEB path and an optional tuple of excluded domain names.
- Produces: path-safe `inspect_feb()`, bytes-safe `inspect_feb_bytes()`, and `FebInspection` with schema/module/units, node and element inventory, materials and canonical parameter hashes, named entities, reference closure, domain geometry summaries, controls/output inventory, `feb-domain-signature-v1`, and the closed `invariant_signatures` keys `domain`, `material`, `reference_closure`, `load`, `boundary`, `contact`, and `output`.

- [ ] **Step 1: Add complete synthetic FEB fixtures**

Create `apps/febio_cae_harness/tests/fixtures/feb/complete-small.feb`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<febio_spec version="4.0">
  <Module type="solid"/>
  <Material>
    <material id="1" name="soft" type="neo-Hookean"><E>1000</E><v>0.3</v></material>
    <material id="2" name="tool" type="rigid body"><density>1</density></material>
  </Material>
  <Mesh>
    <Nodes name="Object01">
      <node id="1">0,0,0</node><node id="2">1,0,0</node>
      <node id="3">0,1,0</node><node id="4">0,0,1</node>
      <node id="5">2,0,0</node>
    </Nodes>
    <Elements type="tet4" name="deformable"><elem id="1">1,2,3,4</elem></Elements>
    <Elements type="tet4" name="rigid"><elem id="2">2,3,4,5</elem></Elements>
    <NodeSet name="fixed"><node id="1"/><node id="3"/></NodeSet>
    <ElementSet name="body-elements"><elem id="1"/></ElementSet>
    <Surface name="primary"><tri3 id="1">1,2,3</tri3></Surface>
    <Surface name="secondary"><tri3 id="1">2,3,4</tri3></Surface>
    <SurfacePair name="pair"><primary>primary</primary><secondary>secondary</secondary></SurfacePair>
  </Mesh>
  <MeshDomains>
    <SolidDomain name="deformable" mat="soft"/>
    <SolidDomain name="rigid" mat="tool"/>
  </MeshDomains>
  <Boundary><bc name="base-fix" type="zero displacement" node_set="fixed"/></Boundary>
  <Loads><surface_load name="push" type="pressure" surface="primary"><pressure lc="1">2</pressure></surface_load></Loads>
  <Contact><contact name="touch" type="sliding-elastic" surface_pair="pair"/></Contact>
  <Rigid><rigid_bc name="tool-fix" type="fix" mat="tool"/></Rigid>
  <LoadData>
    <load_controller id="1" name="ramp" type="loadcurve"><interpolate>LINEAR</interpolate><points><pt>0,0</pt><pt>1,1</pt></points></load_controller>
  </LoadData>
  <Step><step id="1" name="load"><Control><analysis>STATIC</analysis><time_steps>10</time_steps><step_size>0.1</step_size></Control></step></Step>
  <Output><plotfile type="febio"><var type="displacement"/><var type="stress"/></plotfile></Output>
</febio_spec>
```

Create `apps/febio_cae_harness/tests/fixtures/feb/missing-surface.feb`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<febio_spec version="4.0">
  <Module type="solid"/>
  <Material><material id="1" name="soft" type="neo-Hookean"><E>1000</E><v>0.3</v></material></Material>
  <Mesh>
    <Nodes name="Object01">
      <node id="1">0,0,0</node><node id="2">1,0,0</node>
      <node id="3">0,1,0</node><node id="4">0,0,1</node>
    </Nodes>
    <Elements type="tet4" name="deformable"><elem id="1">1,2,3,4</elem></Elements>
  </Mesh>
  <MeshDomains><SolidDomain name="deformable" mat="soft"/></MeshDomains>
  <Loads><surface_load name="broken" type="pressure" surface="absent"/></Loads>
</febio_spec>
```

- [ ] **Step 2: Add inventory, reference, signature, and parser-safety tests**

Create `apps/febio_cae_harness/tests/unit/test_feb_inspector.py`:

```python
from pathlib import Path

import pytest

from febio_cae_harness.feb_inspector import (
    UnsafeFebXml,
    feb_domain_signature,
    inspect_feb,
    inspect_feb_bytes,
)
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.schema import validate_schema

FIXTURES = Path(__file__).parents[1] / "fixtures" / "feb"


def test_complete_inventory_and_reference_closure() -> None:
    path = FIXTURES / "complete-small.feb"
    before = path.read_bytes()
    result = inspect_feb(path)
    assert result.source_sha256 == sha256_file(path)
    assert result.spec_version == "4.0"
    assert result.module == "solid"
    assert result.units == "UNRESOLVED"
    assert result.node_count == 5
    assert result.elements_by_type == {"tet4": 2}
    assert {item["name"] for item in result.materials} == {"soft", "tool"}
    assert result.named_entities["NodeSet"] == ("fixed",)
    assert result.named_entities["ElementSet"] == ("body-elements",)
    assert result.named_entities["Surface"] == ("primary", "secondary")
    assert result.named_entities["SurfacePair"] == ("pair",)
    assert all(reference.resolved for reference in result.references)
    assert result.section_counts == {
        "Boundary": 1,
        "Loads": 1,
        "Contact": 1,
        "Rigid": 1,
        "LoadData": 1,
        "Step": 1,
        "Output": 2,
    }
    assert result.controls == ({
        "analysis": "STATIC",
        "time_steps": "10",
        "step_size": "0.1",
    },)
    deformable = next(
        item for item in result.domains if item["name"] == "deformable"
    )
    assert deformable["bounds"] == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]
    assert deformable["element_type"] == "tet4"
    assert deformable["element_count"] == 1
    assert deformable["referenced_node_count"] == 4
    assert deformable["axis_projection_length"] == 1.0
    assert result.surface_geometry["primary"]["centroid"] == [
        1 / 3,
        1 / 3,
        0.0,
    ]
    assert result.domain_signature_version == "feb-domain-signature-v1"
    assert len(result.domain_signature) == 64
    assert set(result.invariant_signatures) == {
        "domain",
        "material",
        "reference_closure",
        "load",
        "boundary",
        "contact",
        "output",
    }
    base_fix = next(
        item for item in result.physics_items
        if item["section"] == "Boundary" and item["name"] == "base-fix"
    )
    assert base_fix["attributes"]["node_set"] == "fixed"
    pair = next(
        item for item in result.physics_items
        if item["section"] == "SurfacePair" and item["name"] == "pair"
    )
    assert [child["text"] for child in pair["children"]] == [
        "primary",
        "secondary",
    ]
    validate_schema("feb-inspection", result.to_payload())
    assert "nodes" not in result.to_payload()
    assert "elements" not in result.to_payload()
    from_bytes = inspect_feb_bytes(before, source_name="before.feb")
    assert from_bytes.invariant_signatures == result.invariant_signatures
    assert path.read_bytes() == before


def test_signature_excludes_named_domains_and_is_deterministic() -> None:
    path = FIXTURES / "complete-small.feb"
    all_domains = inspect_feb(path)
    excluded = inspect_feb(path, excluded_domains=("rigid",))
    assert all_domains.domain_signature != excluded.domain_signature
    assert excluded.domain_signature == feb_domain_signature(
        excluded.nodes,
        excluded.elements,
        excluded_domains=("rigid",),
    )


def test_surface_selector_used_as_node_set_resolves_to_named_surface() -> None:
    data = (FIXTURES / "complete-small.feb").read_bytes().replace(
        b'node_set="fixed"',
        b'node_set="@surface:primary"',
    )
    result = inspect_feb_bytes(data, source_name="surface-selector.feb")
    reference = next(
        item
        for item in result.references
        if item.source == "@node_set"
    )
    assert reference.target == "@surface:primary"
    assert reference.target_kind == "Surface"
    assert reference.resolved is True


def test_missing_reference_is_explicit() -> None:
    result = inspect_feb(FIXTURES / "missing-surface.feb")
    unresolved = [item for item in result.references if not item.resolved]
    assert len(unresolved) == 1
    assert unresolved[0].target == "absent"
    assert unresolved[0].owner_xml_path.endswith("surface_load[@name='broken']")
    assert unresolved[0].source == "@surface"


@pytest.mark.parametrize(
    "declaration",
    ["<!DOCTYPE febio_spec>", "<!ENTITY injected 'value'>"],
)
def test_dtd_and_entity_are_rejected_before_parse(
    tmp_path: Path,
    declaration: str,
) -> None:
    path = tmp_path / "unsafe.feb"
    path.write_text(
        f"<?xml version='1.0'?>{declaration}<febio_spec version='4.0'/>",
        encoding="utf-8",
    )
    with pytest.raises(UnsafeFebXml, match="DTD_OR_ENTITY_FORBIDDEN"):
        inspect_feb(path)
```

- [ ] **Step 3: Prove RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_feb_inspector.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.feb_inspector'`.

- [ ] **Step 4: Implement streaming numeric extraction and inventory**

Create
`apps/febio_cae_harness/src/febio_cae_harness/feb_inspector.py`:

```python
from __future__ import annotations

import io
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file


class UnsafeFebXml(ValueError):
    """The FEB contains a prohibited DTD or entity declaration."""


@dataclass(frozen=True)
class FebElement:
    domain: str
    element_type: str
    element_id: int
    connectivity: tuple[int, ...]


@dataclass(frozen=True)
class ReferenceRecord:
    owner_xml_path: str
    source: str
    target_kind: str
    target: str
    resolved: bool


@dataclass(frozen=True)
class FebInspection:
    source_path: str
    source_sha256: str
    source_bytes: int
    spec_version: str
    module: str
    units: str
    node_count: int
    nodes: dict[int, tuple[float, float, float]]
    elements: tuple[FebElement, ...]
    elements_by_type: dict[str, int]
    materials: tuple[dict[str, object], ...]
    named_entities: dict[str, tuple[str, ...]]
    domains: tuple[dict[str, object], ...]
    references: tuple[ReferenceRecord, ...]
    section_counts: dict[str, int]
    controls: tuple[dict[str, object], ...]
    output_fields: tuple[str, ...]
    physics_items: tuple[dict[str, object], ...]
    surface_geometry: dict[str, dict[str, list[float]]]
    domain_signature_version: str
    domain_signature: str
    invariant_signatures: dict[str, str]

    def to_payload(self) -> dict[str, object]:
        """Return the closed, compact CLI evidence shape.

        Raw nodes/elements stay available to in-process validators but are
        intentionally excluded from command JSON.
        """
        return {
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "source_bytes": self.source_bytes,
            "spec_version": self.spec_version,
            "module": self.module,
            "units": self.units,
            "node_count": self.node_count,
            "elements_by_type": self.elements_by_type,
            "materials": list(self.materials),
            "named_entities": {
                key: list(value)
                for key, value in self.named_entities.items()
            },
            "domains": list(self.domains),
            "references": [asdict(item) for item in self.references],
            "section_counts": self.section_counts,
            "controls": list(self.controls),
            "output_fields": list(self.output_fields),
            "physics_items": list(self.physics_items),
            "surface_geometry": self.surface_geometry,
            "domain_signature_version": self.domain_signature_version,
            "domain_signature": self.domain_signature,
            "invariant_signatures": self.invariant_signatures,
        }


def _numbers(text: str | None, cast: type[int] | type[float]):
    if text is None:
        return ()
    return tuple(cast(item.strip()) for item in text.split(","))


def _element_path(element: ET.Element) -> str:
    name = element.get("name")
    return (
        f"/febio_spec/{element.tag}[@name='{name}']"
        if name
        else f"/febio_spec/{element.tag}"
    )


def _bounds(points: list[tuple[float, float, float]]) -> list[list[float]]:
    return [
        [min(point[axis] for point in points) for axis in range(3)],
        [max(point[axis] for point in points) for axis in range(3)],
    ]


def _surface_summary(
    connectivity: tuple[int, ...],
    nodes: dict[int, tuple[float, float, float]],
) -> dict[str, list[float]]:
    points = [nodes[node_id] for node_id in connectivity]
    centroid = [
        sum(point[axis] for point in points) / len(points)
        for axis in range(3)
    ]
    first = tuple(points[1][axis] - points[0][axis] for axis in range(3))
    second = tuple(points[2][axis] - points[0][axis] for axis in range(3))
    cross = [
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    ]
    length = math.sqrt(sum(value * value for value in cross))
    normal = [value / length for value in cross] if length else [0.0, 0.0, 0.0]
    return {"centroid": centroid, "normal": normal}


def feb_domain_signature(
    nodes: dict[int, tuple[float, float, float]],
    elements: tuple[FebElement, ...],
    *,
    excluded_domains: tuple[str, ...] = (),
) -> str:
    included = [
        element for element in elements if element.domain not in excluded_domains
    ]
    referenced = {
        node_id for element in included for node_id in element.connectivity
    }
    payload = {
        "nodes": [
            [node_id, [float(value) for value in nodes[node_id]]]
            for node_id in sorted(referenced)
        ],
        "elements": [
            {
                "domain": element.domain,
                "type": element.element_type,
                "id": element.element_id,
                "connectivity": list(element.connectivity),
            }
            for element in included
        ],
    }
    canonical = json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
    )
    return sha256_bytes(canonical.encode("ascii"))


def _xml_value(element: ET.Element) -> dict[str, object]:
    return {
        "tag": element.tag,
        "attributes": dict(sorted(element.attrib.items())),
        "text": (element.text or "").strip(),
        "children": [_xml_value(child) for child in element],
    }


def _signature(value: object) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _inspect_feb_stream(
    stream: io.BytesIO,
    *,
    source_path: str,
    source_sha256: str,
    source_bytes: int,
    excluded_domains: tuple[str, ...] = (),
) -> FebInspection:
    nodes: dict[int, tuple[float, float, float]] = {}
    elements: list[FebElement] = []
    surfaces: dict[str, tuple[int, ...]] = {}
    current_elements: tuple[str, str] | None = None
    current_surface: str | None = None
    root: ET.Element | None = None
    for event, element in ET.iterparse(stream, events=("start", "end")):
        if root is None:
            root = element
        if event == "start" and element.tag == "Elements":
            current_elements = (
                element.get("name", ""),
                element.get("type", ""),
            )
        elif event == "start" and element.tag == "Surface":
            current_surface = element.get("name", "")
        elif event == "end" and element.tag == "node" and element.text:
            node_id = int(element.attrib["id"])
            values = _numbers(element.text, float)
            if len(values) != 3:
                raise ValueError(f"node {node_id} does not have three coordinates")
            nodes[node_id] = values
            element.clear()
        elif (
            event == "end"
            and element.tag == "elem"
            and element.text
            and current_elements is not None
        ):
            elements.append(FebElement(
                domain=current_elements[0],
                element_type=current_elements[1],
                element_id=int(element.attrib["id"]),
                connectivity=_numbers(element.text, int),
            ))
            element.clear()
        elif (
            event == "end"
            and element.tag in {"tri3", "quad4"}
            and element.text
            and current_surface is not None
        ):
            surfaces[current_surface] = _numbers(element.text, int)
            element.clear()
        elif event == "end" and element.tag == "Elements":
            current_elements = None
        elif event == "end" and element.tag == "Surface":
            current_surface = None
    if root is None:
        raise ValueError("empty FEB document")
    module_element = root.find("Module")
    module = "" if module_element is None else module_element.get("type", "")
    material_elements = root.findall("./Material/material")
    materials = tuple({
        "id": int(material.get("id", "0")),
        "name": material.get("name", ""),
        "type": material.get("type", ""),
        "parameter_sha256": sha256_bytes(canonical_json_bytes({
            child.tag: (child.text or "").strip()
            for child in material
        })),
    } for material in material_elements)
    named_entities = {
        kind: tuple(sorted(
            element.get("name", "")
            for element in root.findall(f"./Mesh/{kind}")
        ))
        for kind in ("NodeSet", "ElementSet", "Surface", "SurfacePair")
    }
    named_entities["Domain"] = tuple(
        element.get("name", "")
        for element in root.findall("./MeshDomains/*")
    )
    material_names = {str(item["name"]) for item in materials}
    targets = {
        "node_set": set(named_entities["NodeSet"]),
        "elem_set": set(named_entities["ElementSet"]),
        "surface": set(named_entities["Surface"]),
        "surface_pair": set(named_entities["SurfacePair"]),
        "mat": material_names,
        "lc": {
            element.get("id", "")
            for element in root.findall("./LoadData/load_controller")
        },
    }
    target_kinds = {
        "node_set": "NodeSet",
        "elem_set": "ElementSet",
        "surface": "Surface",
        "surface_pair": "SurfacePair",
        "mat": "Material",
        "lc": "LoadController",
    }
    references: list[ReferenceRecord] = []
    for element in root.iter():
        for attribute in targets:
            target = element.get(attribute)
            if target:
                target_kind = target_kinds[attribute]
                resolved = target in targets[attribute]
                if attribute == "node_set" and target.startswith("@surface:"):
                    target_kind = "Surface"
                    surface_name = target.removeprefix("@surface:")
                    resolved = bool(surface_name) and surface_name in targets["surface"]
                references.append(ReferenceRecord(
                    owner_xml_path=_element_path(element),
                    source=f"@{attribute}",
                    target_kind=target_kind,
                    target=target,
                    resolved=resolved,
                ))
        if element.tag in {"primary", "secondary"} and element.text:
            target = element.text.strip()
            references.append(ReferenceRecord(
                owner_xml_path=f"/febio_spec/SurfacePair/{element.tag}",
                source="text()",
                target_kind="Surface",
                target=target,
                resolved=target in targets["surface"],
            ))
    elements_by_domain = {
        domain: [item for item in elements if item.domain == domain]
        for domain in named_entities["Domain"]
    }
    domains: list[dict[str, object]] = []
    domain_elements = {
        item.get("name", ""): item for item in root.findall("./MeshDomains/*")
    }
    for name, domain_items in elements_by_domain.items():
        referenced = {
            node_id for item in domain_items for node_id in item.connectivity
        }
        points = [nodes[node_id] for node_id in referenced]
        bounds = _bounds(points)
        axis_lengths = [
            bounds[1][axis] - bounds[0][axis] for axis in range(3)
        ]
        axis = max(range(3), key=axis_lengths.__getitem__)
        radial_axes = [item for item in range(3) if item != axis]
        radial_diameter = math.sqrt(sum(
            (bounds[1][item] - bounds[0][item]) ** 2
            for item in radial_axes
        ))
        domain_element = domain_elements[name]
        domains.append({
            "name": name,
            "type": domain_element.tag,
            "material": domain_element.get("mat"),
            "element_type": (
                domain_items[0].element_type
                if len({item.element_type for item in domain_items}) == 1
                else "MIXED"
            ),
            "element_count": len(domain_items),
            "referenced_node_count": len(referenced),
            "bounds": bounds,
            "axis_projection_length": axis_lengths[axis],
            "maximum_radial_diameter": radial_diameter,
        })
    step_elements = root.findall("./Step/step")
    if not step_elements:
        step_elements = [
            item
            for item in root.findall("./Step")
            if item.find("./Control") is not None
        ]
    section_counts = {
        "Boundary": len(root.findall("./Boundary/*")),
        "Loads": len(root.findall("./Loads/*")),
        "Contact": len(root.findall("./Contact/*")),
        "Rigid": len(root.findall("./Rigid/*")),
        "LoadData": len(root.findall("./LoadData/load_controller")),
        "Step": len(step_elements),
        "Output": len(root.findall("./Output/plotfile/var")),
    }
    controls = tuple({
        child.tag: (child.text or "").strip()
        for child in step.findall("./Control/*")
    } for step in step_elements)
    output_fields = tuple(
        item.get("type", "") for item in root.findall("./Output/plotfile/var")
    )
    physics_items = tuple(
        {
            "section": section,
            "tag": item.tag,
            "name": item.get("name", ""),
            "type": item.get("type", ""),
            "attributes": dict(sorted(item.attrib.items())),
            "children": [_xml_value(child) for child in item],
            "tree_sha256": _signature(_xml_value(item)),
        }
        for section, pattern in (
            ("Boundary", "./Boundary/*"),
            ("Loads", "./Loads/*"),
            ("Contact", "./Contact/*"),
            ("Rigid", "./Rigid/*"),
            ("Rigid", "./Step/step/Rigid/*"),
            ("LoadData", "./LoadData/load_controller"),
            ("SurfacePair", "./Mesh/SurfacePair"),
        )
        for item in root.findall(pattern)
    )
    element_tuple = tuple(elements)
    signature = feb_domain_signature(
        nodes,
        element_tuple,
        excluded_domains=excluded_domains,
    )
    invariant_signatures = {
        "domain": signature,
        "material": _signature(materials),
        "reference_closure": _signature([
            {
                "owner_xml_path": item.owner_xml_path,
                "source": item.source,
                "target_kind": item.target_kind,
                "target": item.target,
                "resolved": item.resolved,
            }
            for item in references
        ]),
        "load": _signature([
            _xml_value(item) for item in root.findall("./Loads/*")
        ]),
        "boundary": _signature([
            _xml_value(item) for item in root.findall("./Boundary/*")
        ]),
        "contact": _signature([
            _xml_value(item) for item in root.findall("./Contact/*")
        ]),
        "output": _signature(output_fields),
    }
    return FebInspection(
        source_path=source_path,
        source_sha256=source_sha256,
        source_bytes=source_bytes,
        spec_version=root.get("version", ""),
        module=module,
        units=root.get("units", "UNRESOLVED"),
        node_count=len(nodes),
        nodes=nodes,
        elements=element_tuple,
        elements_by_type={
            kind: sum(item.element_type == kind for item in elements)
            for kind in sorted({item.element_type for item in elements})
        },
        materials=materials,
        named_entities=named_entities,
        domains=tuple(domains),
        references=tuple(references),
        section_counts=section_counts,
        controls=controls,
        output_fields=output_fields,
        physics_items=physics_items,
        surface_geometry={
            name: _surface_summary(connectivity, nodes)
            for name, connectivity in surfaces.items()
        },
        domain_signature_version="feb-domain-signature-v1",
        domain_signature=signature,
        invariant_signatures=invariant_signatures,
    )


def inspect_feb_bytes(
    data: bytes,
    *,
    source_name: str = "<memory>",
    excluded_domains: tuple[str, ...] = (),
) -> FebInspection:
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise UnsafeFebXml("DTD_OR_ENTITY_FORBIDDEN")
    return _inspect_feb_stream(
        io.BytesIO(data),
        source_path=source_name,
        source_sha256=sha256_bytes(data),
        source_bytes=len(data),
        excluded_domains=excluded_domains,
    )


def inspect_feb(
    path: Path,
    *,
    excluded_domains: tuple[str, ...] = (),
) -> FebInspection:
    canonical = path.resolve(strict=True)
    return inspect_feb_bytes(
        canonical.read_bytes(),
        source_name=str(canonical),
        excluded_domains=excluded_domains,
    )
```

- [ ] **Step 5: Add the FEB inspection schema**

Create
`apps/febio_cae_harness/src/febio_cae_harness/schemas/feb-inspection.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/feb-inspection.schema.json",
  "type": "object",
  "required": [
    "source_path", "source_sha256", "source_bytes", "spec_version",
    "module", "units", "node_count", "elements_by_type", "materials",
    "named_entities", "domains", "references", "section_counts",
    "controls", "output_fields", "physics_items", "surface_geometry",
    "domain_signature_version", "domain_signature", "invariant_signatures"
  ],
  "properties": {
    "source_path": {"type": "string", "minLength": 1},
    "source_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "source_bytes": {"type": "integer", "minimum": 1},
    "spec_version": {"type": "string", "minLength": 1},
    "module": {"type": "string", "minLength": 1},
    "units": {"type": "string", "minLength": 1},
    "node_count": {"type": "integer", "minimum": 0},
    "elements_by_type": {
      "type": "object",
      "additionalProperties": {"type": "integer", "minimum": 1}
    },
    "materials": {
      "type": "array",
      "items": {"$ref": "#/$defs/material"}
    },
    "named_entities": {"$ref": "#/$defs/namedEntities"},
    "domains": {
      "type": "array",
      "items": {"$ref": "#/$defs/domain"}
    },
    "references": {
      "type": "array",
      "items": {"$ref": "#/$defs/reference"}
    },
    "section_counts": {"$ref": "#/$defs/sectionCounts"},
    "controls": {
      "type": "array",
      "items": {"$ref": "#/$defs/control"}
    },
    "output_fields": {
      "type": "array",
      "uniqueItems": true,
      "items": {"type": "string", "minLength": 1}
    },
    "physics_items": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "section", "tag", "name", "type", "attributes", "children",
          "tree_sha256"
        ],
        "properties": {
          "section": {
            "enum": [
              "Boundary", "Loads", "Contact", "Rigid", "LoadData",
              "SurfacePair"
            ]
          },
          "tag": {"type": "string", "minLength": 1},
          "name": {"type": "string"},
          "type": {"type": "string"},
          "attributes": {"$ref": "#/$defs/attributes"},
          "children": {
            "type": "array",
            "items": {"$ref": "#/$defs/xmlNode"}
          },
          "tree_sha256": {
            "type": "string", "pattern": "^[0-9A-F]{64}$"
          }
        },
        "additionalProperties": false
      }
    },
    "surface_geometry": {
      "type": "object",
      "additionalProperties": {"$ref": "#/$defs/surfaceGeometry"}
    },
    "domain_signature_version": {"const": "feb-domain-signature-v1"},
    "domain_signature": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "invariant_signatures": {
      "type": "object",
      "required": [
        "domain", "material", "reference_closure", "load",
        "boundary", "contact", "output"
      ],
      "properties": {
        "domain": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "material": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "reference_closure": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "load": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "boundary": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "contact": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "output": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      },
      "additionalProperties": false
    }
  },
  "$defs": {
    "attributes": {
      "type": "object",
      "additionalProperties": {"type": "string"}
    },
    "material": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "name", "type", "parameter_sha256"],
      "properties": {
        "id": {"type": "integer", "minimum": 0},
        "name": {"type": "string", "minLength": 1},
        "type": {"type": "string", "minLength": 1},
        "parameter_sha256": {
          "type": "string",
          "pattern": "^[0-9A-F]{64}$"
        }
      }
    },
    "namedEntities": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "NodeSet", "ElementSet", "Surface", "SurfacePair", "Domain"
      ],
      "properties": {
        "NodeSet": {"$ref": "#/$defs/names"},
        "ElementSet": {"$ref": "#/$defs/names"},
        "Surface": {"$ref": "#/$defs/names"},
        "SurfacePair": {"$ref": "#/$defs/names"},
        "Domain": {"$ref": "#/$defs/names"}
      }
    },
    "names": {
      "type": "array",
      "uniqueItems": true,
      "items": {"type": "string", "minLength": 1}
    },
    "domain": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "name", "type", "material", "element_type", "element_count",
        "referenced_node_count", "bounds", "axis_projection_length",
        "maximum_radial_diameter"
      ],
      "properties": {
        "name": {"type": "string", "minLength": 1},
        "type": {"type": "string", "minLength": 1},
        "material": {"type": ["string", "null"]},
        "element_type": {"type": "string", "minLength": 1},
        "element_count": {"type": "integer", "minimum": 1},
        "referenced_node_count": {"type": "integer", "minimum": 1},
        "bounds": {
          "type": "array",
          "minItems": 2,
          "maxItems": 2,
          "items": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"type": "number"}
          }
        },
        "axis_projection_length": {"type": "number", "minimum": 0},
        "maximum_radial_diameter": {"type": "number", "minimum": 0}
      }
    },
    "reference": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "owner_xml_path", "source", "target_kind", "target", "resolved"
      ],
      "properties": {
        "owner_xml_path": {"type": "string", "minLength": 1},
        "source": {"type": "string", "minLength": 1},
        "target_kind": {
          "enum": [
            "NodeSet", "ElementSet", "Surface", "SurfacePair", "Material",
            "LoadController"
          ]
        },
        "target": {"type": "string", "minLength": 1},
        "resolved": {"type": "boolean"}
      }
    },
    "sectionCounts": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "Boundary", "Loads", "Contact", "Rigid", "LoadData", "Step", "Output"
      ],
      "properties": {
        "Boundary": {"type": "integer", "minimum": 0},
        "Loads": {"type": "integer", "minimum": 0},
        "Contact": {"type": "integer", "minimum": 0},
        "Rigid": {"type": "integer", "minimum": 0},
        "LoadData": {"type": "integer", "minimum": 0},
        "Step": {"type": "integer", "minimum": 0},
        "Output": {"type": "integer", "minimum": 0}
      }
    },
    "control": {
      "type": "object",
      "required": ["time_steps", "step_size"],
      "properties": {
        "time_steps": {"type": "string", "minLength": 1},
        "step_size": {"type": "string", "minLength": 1}
      },
      "propertyNames": {"pattern": "^[A-Za-z_][A-Za-z0-9_.:-]*$"},
      "additionalProperties": {"type": "string"}
    },
    "xmlNode": {
      "type": "object",
      "additionalProperties": false,
      "required": ["tag", "attributes", "text", "children"],
      "properties": {
        "tag": {"type": "string", "minLength": 1},
        "attributes": {"$ref": "#/$defs/attributes"},
        "text": {"type": "string"},
        "children": {
          "type": "array",
          "items": {"$ref": "#/$defs/xmlNode"}
        }
      }
    },
    "surfaceGeometry": {
      "type": "object",
      "additionalProperties": false,
      "required": ["centroid", "normal"],
      "properties": {
        "centroid": {
          "type": "array",
          "minItems": 3,
          "maxItems": 3,
          "items": {"type": "number"}
        },
        "normal": {
          "type": "array",
          "minItems": 3,
          "maxItems": 3,
          "items": {"type": "number"}
        }
      }
    }
  },
  "additionalProperties": false
}
```

Add `"schemas/feb-inspection.schema.json"` to `EXPECTED_RESOURCES`.
Extend `test_feb_inspector.py` to validate the emitted payload and then remove
or change, one at a time, every material `name/type`, domain
`name/element_type/element_count/referenced_node_count`, reference `resolved`,
control `time_steps/step_size`, physics child `tag/text/attributes`, and
output-field value used by Phase 1E. Missing, wrong-type, or extra nested
fields must fail the schema. Keep all fixture names and values synthetic.

- [ ] **Step 6: Verify focused GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_feb_inspector.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: `PASS` with exit code `0`; fixture hashes before and after match and
no output file is created.

- [ ] **Step 7: Commit FEB inspection**

```powershell
git add `
  apps/febio_cae_harness/src/febio_cae_harness/feb_inspector.py `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/feb-inspection.schema.json `
  apps/febio_cae_harness/tests/fixtures/feb/complete-small.feb `
  apps/febio_cae_harness/tests/fixtures/feb/missing-surface.feb `
  apps/febio_cae_harness/tests/unit/test_feb_inspector.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: inspect FEB inputs without mutation"
```

Expected: one commit and a clean `git status --short`.

## Task 11: Inspect STEP read-only and classify inheritance item by item

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/step_inspector.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/inheritance.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/step-inspection.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/inheritance-report.schema.json`
- Create: `apps/febio_cae_harness/tests/fixtures/feb/similar-incompatible.feb`
- Create: `apps/febio_cae_harness/tests/integration/test_step_inspector.py`
- Create: `apps/febio_cae_harness/tests/unit/test_inheritance.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**

- Consumes: a STEP path, optional `gmsh==4.15.2`, two `FebInspection` objects, and explicit category decisions.
- Produces: `inspect_step()` read-only OCC evidence and `build_inheritance_report()` with only `adopted`, `overridden`, `proposed`, `rejected`, or `unresolved` item states across schema, units, domains, materials, sets, references, controls, output fields, and prior verification.

- [ ] **Step 1: Add a real Gmsh STEP read-only test**

Create
`apps/febio_cae_harness/tests/integration/test_step_inspector.py`:

```python
from pathlib import Path

import gmsh

from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.step_inspector import inspect_step


def create_box_step(path: Path) -> None:
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("synthetic-box")
        gmsh.model.occ.addBox(0, 0, 0, 2, 3, 4)
        gmsh.model.occ.synchronize()
        gmsh.write(str(path))
    finally:
        gmsh.finalize()


def test_step_inspection_uses_occ_without_mesh_or_output(tmp_path: Path) -> None:
    path = tmp_path / "box.step"
    create_box_step(path)
    before_names = {item.name for item in tmp_path.iterdir()}
    before_hash = sha256_file(path)
    result = inspect_step(path)
    assert result.source_sha256 == before_hash
    assert result.schema.startswith("CONFIG_CONTROL_DESIGN")
    assert result.volume_count == 1
    assert result.surface_count == 6
    assert result.bounding_box == [0.0, 0.0, 0.0, 2.0, 3.0, 4.0]
    assert result.mass == 24.0
    assert result.center_of_mass == [1.0, 1.5, 2.0]
    assert result.mesh_generated is False
    assert sha256_file(path) == before_hash
    assert {item.name for item in tmp_path.iterdir()} == before_names
```

- [ ] **Step 2: Add incompatible FEB fixture and inheritance tests**

Create `apps/febio_cae_harness/tests/fixtures/feb/similar-incompatible.feb`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<febio_spec version="4.0" units="mm-N-s">
  <Module type="solid"/>
  <Material><material id="1" name="soft" type="neo-Hookean"><E>1500</E><v>0.3</v></material></Material>
  <Mesh>
    <Nodes name="Object01">
      <node id="1">0,0,0</node><node id="2">1,0,0</node>
      <node id="3">0,1,0</node><node id="4">0,0,1</node>
    </Nodes>
    <Elements type="tet4" name="deformable"><elem id="1">1,2,3,4</elem></Elements>
    <NodeSet name="fixed"><node id="1"/></NodeSet>
  </Mesh>
  <MeshDomains><SolidDomain name="deformable" mat="soft"/></MeshDomains>
  <Boundary><bc name="base-fix" type="zero displacement" node_set="fixed"/></Boundary>
  <Step name="load"><Control><analysis>STATIC</analysis><time_steps>5</time_steps><step_size>0.2</step_size></Control></Step>
  <Output><plotfile type="febio"><var type="displacement"/></plotfile></Output>
</febio_spec>
```

Create `apps/febio_cae_harness/tests/unit/test_inheritance.py`:

```python
from pathlib import Path

from febio_cae_harness.feb_inspector import inspect_feb
from febio_cae_harness.inheritance import (
    InheritanceStatus,
    build_inheritance_report,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "feb"


def test_equal_category_is_adopted_and_changed_is_unresolved() -> None:
    base = inspect_feb(FIXTURES / "complete-small.feb")
    candidate = inspect_feb(FIXTURES / "similar-incompatible.feb")
    report = build_inheritance_report(
        base,
        candidate,
        prior_verification_sha256=None,
    )
    by_category = {item.category: item for item in report.items}
    assert by_category["schema"].status is InheritanceStatus.ADOPTED
    assert by_category["units"].status is InheritanceStatus.UNRESOLVED
    assert by_category["materials"].status is InheritanceStatus.UNRESOLVED
    assert by_category["output_fields"].status is InheritanceStatus.UNRESOLVED
    assert by_category["prior_verification"].status is InheritanceStatus.UNRESOLVED
    assert report.has_unresolved is True


def test_explicit_decisions_cover_every_allowed_status() -> None:
    base = inspect_feb(FIXTURES / "complete-small.feb")
    candidate = inspect_feb(FIXTURES / "similar-incompatible.feb")
    decisions = {
        "units": InheritanceStatus.OVERRIDDEN,
        "domains": InheritanceStatus.PROPOSED,
        "materials": InheritanceStatus.REJECTED,
    }
    report = build_inheritance_report(
        base,
        candidate,
        prior_verification_sha256="A" * 64,
        decisions=decisions,
    )
    by_category = {item.category: item.status for item in report.items}
    assert by_category["units"] is InheritanceStatus.OVERRIDDEN
    assert by_category["domains"] is InheritanceStatus.PROPOSED
    assert by_category["materials"] is InheritanceStatus.REJECTED
    assert by_category["prior_verification"] is InheritanceStatus.ADOPTED
    assert {item.value for item in InheritanceStatus} == {
        "adopted", "overridden", "proposed", "rejected", "unresolved"
    }
```

- [ ] **Step 3: Prove RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pip install -e 'apps/febio_cae_harness[dev,step]'
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/integration/test_step_inspector.py `
  apps/febio_cae_harness/tests/unit/test_inheritance.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.step_inspector'`.

- [ ] **Step 4: Implement the optional Gmsh OCC adapter**

Create
`apps/febio_cae_harness/src/febio_cae_harness/step_inspector.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .hashing import sha256_file

_SCHEMA = re.compile(
    r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StepInspection:
    source_path: str
    source_sha256: str
    source_bytes: int
    schema: str
    volume_count: int
    surface_count: int
    bounding_box: list[float]
    mass: float
    center_of_mass: list[float]
    mesh_generated: bool


def inspect_step(path: Path) -> StepInspection:
    canonical = path.resolve(strict=True)
    before_hash = sha256_file(canonical)
    before_bytes = canonical.stat().st_size
    header = canonical.read_text(
        encoding="ascii",
        errors="ignore",
    )[:262144]
    match = _SCHEMA.search(header)
    schema = "UNRESOLVED" if match is None else match.group(1)
    try:
        import gmsh
    except ImportError as error:
        raise RuntimeError(
            "STEP_INSPECTOR_UNAVAILABLE: install the step extra"
        ) from error
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("read-only-step-inspection")
        gmsh.model.occ.importShapes(str(canonical), highestDimOnly=False)
        gmsh.model.occ.synchronize()
        volumes = gmsh.model.getEntities(3)
        surfaces = gmsh.model.getEntities(2)
        boxes = [gmsh.model.getBoundingBox(dim, tag) for dim, tag in volumes]
        if not boxes:
            raise ValueError("STEP_HAS_NO_VOLUME")
        bounds = [
            min(item[0] for item in boxes),
            min(item[1] for item in boxes),
            min(item[2] for item in boxes),
            max(item[3] for item in boxes),
            max(item[4] for item in boxes),
            max(item[5] for item in boxes),
        ]
        masses = [gmsh.model.occ.getMass(dim, tag) for dim, tag in volumes]
        centers = [
            gmsh.model.occ.getCenterOfMass(dim, tag) for dim, tag in volumes
        ]
        mass = sum(masses)
        center = [
            sum(masses[index] * centers[index][axis] for index in range(len(masses)))
            / mass
            for axis in range(3)
        ]
        mesh_nodes = gmsh.model.mesh.getNodes()[0]
        result = StepInspection(
            source_path=str(canonical),
            source_sha256=before_hash,
            source_bytes=before_bytes,
            schema=schema,
            volume_count=len(volumes),
            surface_count=len(surfaces),
            bounding_box=[float(value) for value in bounds],
            mass=float(mass),
            center_of_mass=[float(value) for value in center],
            mesh_generated=len(mesh_nodes) != 0,
        )
    finally:
        gmsh.finalize()
    if (
        canonical.stat().st_size != before_bytes
        or sha256_file(canonical) != before_hash
    ):
        raise OSError("STEP_SOURCE_MUTATED_DURING_INSPECTION")
    return result
```

- [ ] **Step 5: Implement item-level inheritance**

Create
`apps/febio_cae_harness/src/febio_cae_harness/inheritance.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .feb_inspector import FebInspection
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file


class InheritanceStatus(StrEnum):
    ADOPTED = "adopted"
    OVERRIDDEN = "overridden"
    PROPOSED = "proposed"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class InheritanceItem:
    category: str
    base_sha256: str | None
    candidate_sha256: str | None
    status: InheritanceStatus
    reason: str


@dataclass(frozen=True)
class InheritanceReport:
    schema_version: int
    items: tuple[InheritanceItem, ...]
    has_unresolved: bool


def _digest(value: object) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def build_inheritance_report(
    base: FebInspection,
    candidate: FebInspection,
    *,
    prior_verification_sha256: str | None,
    decisions: dict[str, InheritanceStatus] | None = None,
) -> InheritanceReport:
    decisions = {} if decisions is None else decisions
    categories = {
        "schema": (base.spec_version, candidate.spec_version),
        "units": (base.units, candidate.units),
        "domains": (base.domains, candidate.domains),
        "materials": (base.materials, candidate.materials),
        "sets": (base.named_entities, candidate.named_entities),
        "references": (
            [item.__dict__ for item in base.references],
            [item.__dict__ for item in candidate.references],
        ),
        "controls": (base.controls, candidate.controls),
        "output_fields": (base.output_fields, candidate.output_fields),
        "prior_verification": (
            prior_verification_sha256,
            prior_verification_sha256,
        ),
    }
    items: list[InheritanceItem] = []
    for category, (base_value, candidate_value) in categories.items():
        base_digest = None if base_value is None else _digest(base_value)
        candidate_digest = (
            None if candidate_value is None else _digest(candidate_value)
        )
        if category in decisions:
            status = decisions[category]
            reason = "explicit Analysis Intent inheritance decision"
        elif base_digest is not None and base_digest == candidate_digest:
            status = InheritanceStatus.ADOPTED
            reason = "canonical category digests match"
        else:
            status = InheritanceStatus.UNRESOLVED
            reason = "category differs or has no prior evidence"
        items.append(InheritanceItem(
            category=category,
            base_sha256=base_digest,
            candidate_sha256=candidate_digest,
            status=status,
            reason=reason,
        ))
    return InheritanceReport(
        schema_version=1,
        items=tuple(items),
        has_unresolved=any(
            item.status is InheritanceStatus.UNRESOLVED for item in items
        ),
    )
```

- [ ] **Step 6: Add STEP and inheritance schemas**

Create `schemas/step-inspection.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/step-inspection.schema.json",
  "type": "object",
  "required": [
    "source_path", "source_sha256", "source_bytes", "schema",
    "volume_count", "surface_count", "bounding_box", "mass",
    "center_of_mass", "mesh_generated"
  ],
  "properties": {
    "source_path": {"type": "string", "minLength": 1},
    "source_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "source_bytes": {"type": "integer", "minimum": 1},
    "schema": {"type": "string", "minLength": 1},
    "volume_count": {"type": "integer", "minimum": 0},
    "surface_count": {"type": "integer", "minimum": 0},
    "bounding_box": {
      "type": "array", "minItems": 6, "maxItems": 6,
      "items": {"type": "number"}
    },
    "mass": {"type": "number", "exclusiveMinimum": 0},
    "center_of_mass": {
      "type": "array", "minItems": 3, "maxItems": 3,
      "items": {"type": "number"}
    },
    "mesh_generated": {"const": false}
  },
  "additionalProperties": false
}
```

Create `schemas/inheritance-report.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/inheritance-report.schema.json",
  "type": "object",
  "required": ["schema_version", "items", "has_unresolved"],
  "properties": {
    "schema_version": {"const": 1},
    "items": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "category", "base_sha256", "candidate_sha256", "status", "reason"
        ],
        "properties": {
          "category": {"type": "string", "minLength": 1},
          "base_sha256": {"type": ["string", "null"]},
          "candidate_sha256": {"type": ["string", "null"]},
          "status": {
            "enum": [
              "adopted", "overridden", "proposed", "rejected", "unresolved"
            ]
          },
          "reason": {"type": "string", "minLength": 1}
        },
        "additionalProperties": false
      }
    },
    "has_unresolved": {"type": "boolean"}
  },
  "additionalProperties": false
}
```

Add both schema paths to `EXPECTED_RESOURCES`.

- [ ] **Step 7: Verify focused GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/integration/test_step_inspector.py `
  apps/febio_cae_harness/tests/unit/test_inheritance.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: `PASS` with exit code `0`; STEP directory inventory and STEP hash are
unchanged by `inspect_step()`.

- [ ] **Step 8: Commit geometry inspection and inheritance**

```powershell
git add `
  apps/febio_cae_harness/src/febio_cae_harness/step_inspector.py `
  apps/febio_cae_harness/src/febio_cae_harness/inheritance.py `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/step-inspection.schema.json `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/inheritance-report.schema.json `
  apps/febio_cae_harness/tests/fixtures/feb/similar-incompatible.feb `
  apps/febio_cae_harness/tests/integration/test_step_inspector.py `
  apps/febio_cae_harness/tests/unit/test_inheritance.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: inspect STEP and report inheritance"
```

Expected: one commit and a clean `git status --short`.

## Task 12: Define and validate the complete Analysis Intent Contract

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/intent.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/analysis-intent.schema.json`
- Create: `apps/febio_cae_harness/tests/contract/test_intent_schema.py`
- Create: `apps/febio_cae_harness/tests/unit/test_intent.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**

- Consumes: an Analysis Intent mapping, FEB inspection mapping, and candidate values at the five authority levels.
- Produces: `SettingProvenance`, `resolve_setting()`, and `validate_intent()`; the authority order is current user, approved contract, compatible base FEB, similar FEB, then default, and any critical unresolved item blocks approval.

- [ ] **Step 1: Add a complete schema fixture and validation tests**

Create `apps/febio_cae_harness/tests/contract/test_intent_schema.py`:

```python
from febio_cae_harness.schema import validate_schema


def complete_contract() -> dict[str, object]:
    resolved = lambda value: {
        "resolved": True,
        "value": value,
    }
    return {
        "schema_version": 1,
        "analysis_id": "analysis-001",
        "engineering_question": "Determine displacement and stress under load.",
        "parts_and_roles": resolved({"specimen": "deformable", "tool": "rigid"}),
        "unit_system": resolved("mm-N-s"),
        "materials": resolved({"specimen": "synthetic-elastic"}),
        "loads": resolved([{"kind": "pressure", "magnitude": 2.0}]),
        "constraints": resolved([{"kind": "fixed", "set": "fixed"}]),
        "contacts": resolved([{"pair": "pair", "kind": "sliding"}]),
        "analysis_steps": resolved({"steps": 10, "time": 1.0}),
        "roi_and_protected_geometry": resolved({
            "roi": ["deformable"],
            "protected": ["primary", "secondary"],
        }),
        "result_requests": resolved({
            "population": ["displacement", "stress"],
        }),
        "load_path_and_physical_assumptions": resolved([
            "quasi-static loading",
        ]),
        "invariants": resolved([
            "domain signature",
            "material signature",
            "reference closure",
        ]),
        "allowed_numerical_changes": [{
            "name": "temporary diagnostic step size",
            "selector_id": "solid.max_refs",
            "allowed_target_values": [30, 50],
            "retry_budget": 1,
            "purpose": "diagnostic",
            "eligible_for_promotion": False,
        }],
        "mesh_and_result_comparison_criteria": resolved({
            "mesh": "converged",
            "result": "within approved tolerance",
        }),
        "human_approval_required_for": [
            "source selection",
            "analysis intent",
            "intent-impacting change",
        ],
        "unresolved_items": [],
        "prohibited_conclusions": [
            "manufacturing validation from screening evidence",
        ],
        "setting_provenance": [{
            "path": "/unit_system",
            "state": "USER_SPECIFIED",
            "source": "current user",
        }],
        "expected_run": resolved({
            "solver_version": "4.12.0",
            "time_steps": 10,
            "end_time": 1.0,
            "required_fbs_state_count": 11,
            "automatic_retry_budget": 1,
            "execution_request_sha256": "E" * 64,
            "required_result_population": ["displacement", "stress"],
        }),
        "kinematic_checks": resolved([
            "rigid-body motion constrained",
            "load path connected",
        ]),
    }


def test_complete_contract_matches_installed_schema() -> None:
    validate_schema("analysis-intent", complete_contract())
```

Create `apps/febio_cae_harness/tests/unit/test_intent.py`:

```python
import copy

import pytest

from febio_cae_harness.intent import (
    SettingProvenance,
    resolve_setting,
    validate_intent,
)
from test_intent_schema import complete_contract


@pytest.mark.parametrize(
    "section",
    [
        "unit_system",
        "materials",
        "loads",
        "constraints",
        "contacts",
        "analysis_steps",
        "result_requests",
        "expected_run",
    ],
)
def test_critical_unresolved_section_blocks_approval(section: str) -> None:
    contract = complete_contract()
    contract[section] = {"resolved": False, "value": None}
    validation = validate_intent(contract, {"references": []})
    assert validation.approvable is False
    assert f"CRITICAL_UNRESOLVED:{section}" in {
        item.code for item in validation.issues
    }


def test_unresolved_feb_reference_blocks_approval() -> None:
    contract = complete_contract()
    validation = validate_intent(contract, {
        "references": [{
            "owner_xml_path": "/febio_spec/Loads/surface_load",
            "target": "missing",
            "resolved": False,
        }]
    })
    assert validation.approvable is False
    assert "FEB_REFERENCE_UNRESOLVED" in {
        item.code for item in validation.issues
    }


def test_operational_naming_default_does_not_block() -> None:
    contract = complete_contract()
    contract["unresolved_items"] = [{
        "path": "/operational/output_base_name",
        "severity": "operational",
        "description": "Use deterministic default.",
    }]
    assert validate_intent(contract, {"references": []}).approvable is True


def test_numerical_assumption_is_diagnostic_and_never_promotable() -> None:
    contract = complete_contract()
    assert validate_intent(contract, {"references": []}).approvable is True
    invalid = copy.deepcopy(contract)
    invalid["allowed_numerical_changes"][0]["eligible_for_promotion"] = True
    result = validate_intent(invalid, {"references": []})
    assert result.approvable is False
    assert "NUMERICAL_ASSUMPTION_NOT_DIAGNOSTIC" in {
        item.code for item in result.issues
    }


def test_setting_priority_and_provenance_are_exact() -> None:
    selected = resolve_setting(
        current_user=None,
        approved_contract=None,
        compatible_base_feb=3,
        similar_feb=4,
        default=5,
    )
    assert selected.value == 3
    assert selected.provenance is SettingProvenance.INHERITED
    assert selected.authority == "compatible_base_feb"
    selected = resolve_setting(
        current_user=1,
        approved_contract=2,
        compatible_base_feb=3,
        similar_feb=4,
        default=5,
    )
    assert selected.value == 1
    assert selected.provenance is SettingProvenance.USER_SPECIFIED
```

- [ ] **Step 2: Prove RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/contract/test_intent_schema.py `
  apps/febio_cae_harness/tests/unit/test_intent.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.intent'`.

- [ ] **Step 3: Implement priority resolution and critical validation**

Create `apps/febio_cae_harness/src/febio_cae_harness/intent.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


REQUIRED_INTENT_SECTIONS = (
    "engineering_question",
    "parts_and_roles",
    "unit_system",
    "materials",
    "loads",
    "constraints",
    "contacts",
    "analysis_steps",
    "roi_and_protected_geometry",
    "result_requests",
    "load_path_and_physical_assumptions",
    "invariants",
    "allowed_numerical_changes",
    "mesh_and_result_comparison_criteria",
    "human_approval_required_for",
    "unresolved_items",
    "prohibited_conclusions",
    "setting_provenance",
    "expected_run",
    "kinematic_checks",
)
CRITICAL_SECTIONS = (
    "unit_system",
    "materials",
    "loads",
    "constraints",
    "contacts",
    "analysis_steps",
    "result_requests",
    "expected_run",
)


class SettingProvenance(StrEnum):
    USER_SPECIFIED = "USER_SPECIFIED"
    INHERITED = "INHERITED"
    OVERRIDDEN = "OVERRIDDEN"
    INFERRED = "INFERRED"
    UNRESOLVED = "UNRESOLVED"
    PROHIBITED = "PROHIBITED"


@dataclass(frozen=True)
class ResolvedSetting:
    value: object
    provenance: SettingProvenance
    authority: str


@dataclass(frozen=True)
class IntentIssue:
    code: str
    path: str
    message: str
    critical: bool


@dataclass(frozen=True)
class IntentValidation:
    approvable: bool
    issues: tuple[IntentIssue, ...]


def resolve_setting(
    *,
    current_user: object | None,
    approved_contract: object | None,
    compatible_base_feb: object | None,
    similar_feb: object | None,
    default: object | None,
) -> ResolvedSetting:
    ordered = (
        ("current_user", current_user, SettingProvenance.USER_SPECIFIED),
        ("approved_contract", approved_contract, SettingProvenance.OVERRIDDEN),
        ("compatible_base_feb", compatible_base_feb, SettingProvenance.INHERITED),
        ("similar_feb", similar_feb, SettingProvenance.INFERRED),
        ("default", default, SettingProvenance.INFERRED),
    )
    for authority, value, provenance in ordered:
        if value is not None:
            return ResolvedSetting(value, provenance, authority)
    return ResolvedSetting(
        None,
        SettingProvenance.UNRESOLVED,
        "none",
    )


def _resolved(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("resolved") is not True:
        return False
    actual = value.get("value")
    return actual is not None and actual != "" and actual != [] and actual != {}


def validate_intent(
    contract: dict[str, object],
    inspection: dict[str, object],
) -> IntentValidation:
    issues: list[IntentIssue] = []
    for section in REQUIRED_INTENT_SECTIONS:
        if section not in contract:
            issues.append(IntentIssue(
                "REQUIRED_SECTION_MISSING",
                f"/{section}",
                f"required Analysis Intent section is missing: {section}",
                True,
            ))
    for section in CRITICAL_SECTIONS:
        if not _resolved(contract.get(section)):
            issues.append(IntentIssue(
                f"CRITICAL_UNRESOLVED:{section}",
                f"/{section}",
                f"critical section is unresolved: {section}",
                True,
            ))
    for item in contract.get("unresolved_items", []):
        if (
            isinstance(item, dict)
            and item.get("severity") == "critical"
        ):
            issues.append(IntentIssue(
                "CRITICAL_UNRESOLVED_ITEM",
                str(item.get("path", "/unresolved_items")),
                str(item.get("description", "critical item is unresolved")),
                True,
            ))
    for reference in inspection.get("references", []):
        if isinstance(reference, dict) and reference.get("resolved") is not True:
            issues.append(IntentIssue(
                "FEB_REFERENCE_UNRESOLVED",
                str(reference.get("owner_xml_path", "/inspection/references")),
                f"FEB reference is unresolved: {reference.get('target')}",
                True,
            ))
    for index, change in enumerate(
        contract.get("allowed_numerical_changes", [])
    ):
        valid = (
            isinstance(change, dict)
            and change.get("purpose") == "diagnostic"
            and change.get("eligible_for_promotion") is False
        )
        if not valid:
            issues.append(IntentIssue(
                "NUMERICAL_ASSUMPTION_NOT_DIAGNOSTIC",
                f"/allowed_numerical_changes/{index}",
                "numerical assumptions must be diagnostic and non-promotable",
                True,
            ))
    return IntentValidation(
        approvable=not any(item.critical for item in issues),
        issues=tuple(issues),
    )
```

- [ ] **Step 4: Add the complete Analysis Intent schema**

Create
`apps/febio_cae_harness/src/febio_cae_harness/schemas/analysis-intent.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/analysis-intent.schema.json",
  "description": "Binding is auditable but does not authenticate the human speaker.",
  "type": "object",
  "required": [
    "schema_version", "analysis_id", "engineering_question",
    "parts_and_roles", "unit_system", "materials", "loads", "constraints",
    "contacts", "analysis_steps", "roi_and_protected_geometry",
    "result_requests", "load_path_and_physical_assumptions", "invariants",
    "allowed_numerical_changes", "mesh_and_result_comparison_criteria",
    "human_approval_required_for", "unresolved_items",
    "prohibited_conclusions", "setting_provenance", "expected_run",
    "kinematic_checks"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "analysis_id": {"type": "string", "minLength": 1},
    "engineering_question": {"type": "string", "minLength": 1},
    "parts_and_roles": {"$ref": "#/$defs/resolved"},
    "unit_system": {"$ref": "#/$defs/resolved"},
    "materials": {"$ref": "#/$defs/resolved"},
    "loads": {"$ref": "#/$defs/resolved"},
    "constraints": {"$ref": "#/$defs/resolved"},
    "contacts": {"$ref": "#/$defs/resolved"},
    "analysis_steps": {"$ref": "#/$defs/resolved"},
    "roi_and_protected_geometry": {"$ref": "#/$defs/resolved"},
    "result_requests": {"$ref": "#/$defs/resolved"},
    "load_path_and_physical_assumptions": {"$ref": "#/$defs/resolved"},
    "invariants": {"$ref": "#/$defs/resolved"},
    "allowed_numerical_changes": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "name", "selector_id", "retry_budget", "purpose",
          "eligible_for_promotion"
        ],
        "properties": {
          "name": {"type": "string", "minLength": 1},
          "selector_id": {"type": "string", "minLength": 1},
          "allowed_target_values": {
            "type": "array", "minItems": 1, "uniqueItems": true,
            "items": {}
          },
          "allowed_multipliers": {
            "type": "array", "minItems": 1, "uniqueItems": true,
            "items": {"type": "number", "exclusiveMinimum": 0}
          },
          "retry_budget": {
            "type": "integer", "minimum": 0, "maximum": 3
          },
          "purpose": {"const": "diagnostic"},
          "eligible_for_promotion": {"const": false}
        },
        "anyOf": [
          {"required": ["allowed_target_values"]},
          {"required": ["allowed_multipliers"]}
        ],
        "additionalProperties": false
      }
    },
    "mesh_and_result_comparison_criteria": {"$ref": "#/$defs/resolved"},
    "human_approval_required_for": {
      "type": "array", "minItems": 1,
      "items": {"type": "string", "minLength": 1}
    },
    "unresolved_items": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["path", "severity", "description"],
        "properties": {
          "path": {"type": "string", "minLength": 1},
          "severity": {"enum": ["critical", "operational"]},
          "description": {"type": "string", "minLength": 1}
        },
        "additionalProperties": false
      }
    },
    "prohibited_conclusions": {
      "type": "array", "minItems": 1,
      "items": {"type": "string", "minLength": 1}
    },
    "setting_provenance": {
      "type": "array", "minItems": 1,
      "items": {
        "type": "object",
        "required": ["path", "state", "source"],
        "properties": {
          "path": {"type": "string", "minLength": 1},
          "state": {
            "enum": [
              "USER_SPECIFIED", "INHERITED", "OVERRIDDEN",
              "INFERRED", "UNRESOLVED", "PROHIBITED"
            ]
          },
          "source": {"type": "string", "minLength": 1}
        },
        "additionalProperties": false
      }
    },
    "expected_run": {"$ref": "#/$defs/expectedRun"},
    "kinematic_checks": {"$ref": "#/$defs/resolved"}
  },
  "$defs": {
    "resolved": {
      "type": "object",
      "required": ["resolved", "value"],
      "properties": {
        "resolved": {"type": "boolean"},
        "value": {}
      },
      "additionalProperties": false
    },
    "expectedRun": {
      "type": "object",
      "additionalProperties": false,
      "required": ["resolved", "value"],
      "properties": {
        "resolved": {"const": true},
        "value": {
          "type": "object",
          "additionalProperties": false,
          "required": [
            "solver_version", "time_steps", "end_time",
            "required_fbs_state_count", "automatic_retry_budget",
            "execution_request_sha256", "required_result_population"
          ],
          "properties": {
            "solver_version": {"type": "string", "minLength": 1},
            "time_steps": {"type": "integer", "minimum": 1},
            "end_time": {"type": "number", "exclusiveMinimum": 0},
            "required_fbs_state_count": {"type": "integer", "minimum": 1},
            "automatic_retry_budget": {
              "type": "integer", "minimum": 0, "maximum": 3
            },
            "execution_request_sha256": {
              "type": "string", "pattern": "^[0-9A-F]{64}$"
            },
            "required_result_population": {
              "type": "array",
              "minItems": 1,
              "uniqueItems": true,
              "items": {"type": "string", "minLength": 1}
            }
          }
        }
      }
    }
  },
  "additionalProperties": false
}
```

`execution_request_sha256` is the uppercase SHA-256 of Phase 1C
`canonical_json_bytes()` over the complete closed external execution request.
It binds solver path/hash, all timeout/resource/tolerance/allowance values, and
every requested LOG/FBS time before Analysis Intent approval.

Add `"schemas/analysis-intent.schema.json"` to `EXPECTED_RESOURCES`.

- [ ] **Step 5: Verify focused GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/contract/test_intent_schema.py `
  apps/febio_cae_harness/tests/unit/test_intent.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: `PASS` with exit code `0`.

- [ ] **Step 6: Commit Analysis Intent validation**

```powershell
git add `
  apps/febio_cae_harness/src/febio_cae_harness/intent.py `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/analysis-intent.schema.json `
  apps/febio_cae_harness/tests/contract/test_intent_schema.py `
  apps/febio_cae_harness/tests/unit/test_intent.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: validate complete analysis intent"
```

Expected: one commit and a clean `git status --short`.

## Task 13: Version Analysis Intent and bind one-time human approval

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/approval.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/approval-request.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/approval-record.schema.json`
- Create: `apps/febio_cae_harness/tests/unit/test_approval.py`
- Modify: `apps/febio_cae_harness/src/febio_cae_harness/input_store.py`
- Modify: `apps/febio_cae_harness/README.md`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**

- Consumes: `CaseStore`, validated contract, current `InputRecord` set, optional source-selection approval record, execution-profile SHA-256, one-time nonce, exact nonblank human text, human actor kind, channel, and UTC time.
- Produces: `input_record_set_digest()`, immutable `IntentRevision`, `ApprovalRequest`, and `ApprovalRecord`; approval is bound to purpose `analysis-intent`, analysis/revision/contract/input/source/execution hashes and moves only `INTENT_DRAFTED -> INTENT_APPROVED`.

- [ ] **Step 1: Add success, drift, replay, and revision-retention tests**

Create `apps/febio_cae_harness/tests/unit/test_approval.py`:

```python
from pathlib import Path

import pytest

from febio_cae_harness.approval import (
    ApprovalBindingError,
    create_revision,
    record_approval,
    request_approval,
)
from febio_cae_harness.case_state import CaseState
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.input_store import input_record_set_digest
from test_intent_schema import complete_contract


def drafted_case(tmp_path: Path):
    store = CaseStore.initialize(tmp_path / "case", "analysis-001")
    store.append(
        "INPUT_INSPECTED",
        CaseState.INPUT_INSPECTED,
        {"input_record_set_digest": input_record_set_digest(store.case_dir)},
    )
    revision = create_revision(
        store,
        complete_contract(),
        inspection={"references": []},
        source_selection_approval_record=None,
    )
    return store, revision


def test_approval_binds_all_execution_authority(tmp_path: Path) -> None:
    store, revision = drafted_case(tmp_path)
    request = request_approval(
        store,
        revision.revision,
        execution_profile_sha256="E" * 64,
        nonce="analysis-intent-nonce-001",
        requested_at="2026-07-30T00:00:00Z",
    )
    record = record_approval(
        store,
        request.request_path,
        presented_nonce="analysis-intent-nonce-001",
        approval_text="I approve Analysis Intent revision 1 for execution.",
        actor_kind="human",
        source_channel="codex-desktop",
        approved_at="2026-07-30T00:01:00Z",
        current_execution_profile_sha256="E" * 64,
        current_source_selection_approval_record=None,
    )
    assert record.purpose == "analysis-intent"
    assert record.contract_sha256 == revision.contract_sha256
    assert record.input_record_set_digest == input_record_set_digest(
        store.case_dir
    )
    assert store.replay()["harness"]["case_state"] == "INTENT_APPROVED"
    assert list((store.case_dir / "02_Model").iterdir()) == []


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("nonce", "nonce"),
        ("execution", "execution profile"),
        ("input", "InputRecord"),
        ("source", "source-selection"),
        ("text", "approval text"),
        ("actor", "actor"),
    ],
)
def test_drift_and_nonhuman_authority_fail_closed(
    tmp_path: Path,
    field: str,
    message: str,
) -> None:
    store, revision = drafted_case(tmp_path)
    request = request_approval(
        store,
        revision.revision,
        execution_profile_sha256="E" * 64,
        nonce="analysis-intent-nonce-001",
        requested_at="2026-07-30T00:00:00Z",
    )
    if field == "input":
        (store.case_dir / "01_Input" / "external-source-record-drift.json").write_text(
            '{"record_id":"drift"}\n',
            encoding="utf-8",
        )
    source_record = None
    if field == "source":
        source_record = store.case_dir / "01_Input" / "source-selection-drift.json"
        source_record.write_text('{"purpose":"SOURCE_SELECTION"}\n', encoding="utf-8")
    with pytest.raises(ApprovalBindingError, match=message):
        record_approval(
            store,
            request.request_path,
            presented_nonce=(
                "wrong" if field == "nonce" else "analysis-intent-nonce-001"
            ),
            approval_text=" " if field == "text" else "I approve revision 1.",
            actor_kind="agent" if field == "actor" else "human",
            source_channel="codex-desktop",
            approved_at="2026-07-30T00:01:00Z",
            current_execution_profile_sha256=(
                "F" * 64 if field == "execution" else "E" * 64
            ),
            current_source_selection_approval_record=source_record,
        )


def test_nonce_replay_is_rejected(tmp_path: Path) -> None:
    store, revision = drafted_case(tmp_path)
    request = request_approval(
        store,
        revision.revision,
        execution_profile_sha256="E" * 64,
        nonce="analysis-intent-nonce-001",
        requested_at="2026-07-30T00:00:00Z",
    )
    arguments = {
        "presented_nonce": "analysis-intent-nonce-001",
        "approval_text": "I approve revision 1.",
        "actor_kind": "human",
        "source_channel": "codex-desktop",
        "approved_at": "2026-07-30T00:01:00Z",
        "current_execution_profile_sha256": "E" * 64,
        "current_source_selection_approval_record": None,
    }
    record_approval(store, request.request_path, **arguments)
    with pytest.raises(ApprovalBindingError, match="already used"):
        record_approval(store, request.request_path, **arguments)


def test_post_approval_edit_creates_n_plus_one_and_retains_prior(
    tmp_path: Path,
) -> None:
    store, first = drafted_case(tmp_path)
    request = request_approval(
        store,
        first.revision,
        execution_profile_sha256="E" * 64,
        nonce="analysis-intent-nonce-001",
        requested_at="2026-07-30T00:00:00Z",
    )
    record_approval(
        store,
        request.request_path,
        presented_nonce="analysis-intent-nonce-001",
        approval_text="I approve revision 1.",
        actor_kind="human",
        source_channel="codex-desktop",
        approved_at="2026-07-30T00:01:00Z",
        current_execution_profile_sha256="E" * 64,
        current_source_selection_approval_record=None,
    )
    changed = complete_contract()
    changed["engineering_question"] = "Revised engineering question."
    second = create_revision(
        store,
        changed,
        inspection={"references": []},
        source_selection_approval_record=None,
    )
    assert second.revision == 2
    assert first.revision_path.exists()
    assert second.revision_path.exists()
    assert store.replay()["harness"]["case_state"] == "INTENT_DRAFTED"
```

- [ ] **Step 2: Prove RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_approval.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.approval'`.

- [ ] **Step 3: Add the canonical InputRecord set digest**

Append to `apps/febio_cae_harness/src/febio_cae_harness/input_store.py`:

```python
def input_record_set_digest(case_dir: Path) -> str:
    input_dir = case_dir / "01_Input"
    records = []
    for pattern in (
        "authoritative-feb-*.json",
        "external-source-record-*.json",
        "baseline-result-reference-*.json",
    ):
        for path in input_dir.glob(pattern):
            records.append({
                "relative_path": path.relative_to(case_dir).as_posix(),
                "sha256": sha256_bytes(path.read_bytes()),
                "bytes": path.stat().st_size,
            })
    return sha256_bytes(canonical_json_bytes(sorted(
        records,
        key=lambda item: item["relative_path"],
    )))
```

- [ ] **Step 4: Implement revision and approval binding**

Create `apps/febio_cae_harness/src/febio_cae_harness/approval.py`:

```python
from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from .case_state import CaseState
from .case_store import CaseStore
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .input_store import input_record_set_digest
from .intent import validate_intent
from .jsonio import ArtifactCollisionError, atomic_create_unique_artifact
from .schema import validate_schema

_REVISION = re.compile(r"analysis-intent-r(\d{4})\.json\Z")


class ApprovalBindingError(ValueError):
    """Analysis Intent approval fields do not match the bound request."""


@dataclass(frozen=True)
class IntentRevision:
    schema_version: int
    analysis_id: str
    revision: int
    contract_sha256: str
    input_record_set_digest: str
    source_selection_approval_record_digest: str | None
    revision_path: Path


@dataclass(frozen=True)
class ApprovalRequest:
    schema_version: int
    purpose: str
    request_id: str
    analysis_id: str
    revision: int
    contract_sha256: str
    input_record_set_digest: str
    source_selection_approval_record_digest: str | None
    execution_profile_sha256: str
    nonce: str
    requested_at: str
    request_path: Path


@dataclass(frozen=True)
class ApprovalRecord:
    schema_version: int
    purpose: str
    request_id: str
    analysis_id: str
    revision: int
    contract_sha256: str
    input_record_set_digest: str
    source_selection_approval_record_digest: str | None
    execution_profile_sha256: str
    nonce: str
    approval_text: str
    actor_kind: str
    source_channel: str
    approved_at: str
    record_path: Path


def _intent_dir(store: CaseStore) -> Path:
    return store.case_dir / "05_Verification" / "harness" / "intent"


def _source_digest(path: Path | None) -> str | None:
    return None if path is None else sha256_file(path.resolve(strict=True))


def _latest_revision(store: CaseStore) -> tuple[int, Path] | None:
    found = []
    for path in _intent_dir(store).glob("analysis-intent-r*.json"):
        match = _REVISION.fullmatch(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return None if not found else max(found, key=lambda item: item[0])


def create_revision(
    case: CaseStore,
    contract: dict[str, object],
    *,
    inspection: dict[str, object],
    source_selection_approval_record: Path | None,
) -> IntentRevision:
    validate_schema("analysis-intent", contract)
    validation = validate_intent(contract, inspection)
    if not validation.approvable:
        codes = ",".join(item.code for item in validation.issues if item.critical)
        raise ApprovalBindingError(f"INTENT_HAS_CRITICAL_BLOCKERS:{codes}")
    manifest = case.replay()
    analysis_id = str(manifest.get("analysis_id", ""))
    if contract["analysis_id"] != analysis_id:
        raise ApprovalBindingError("analysis_id drift")
    current = CaseState(manifest["harness"]["case_state"])
    if current not in {CaseState.INPUT_INSPECTED, CaseState.INTENT_APPROVED}:
        raise ApprovalBindingError(
            f"revision cannot be drafted from {current.value}"
        )
    latest = _latest_revision(case)
    revision_number = 1 if latest is None else latest[0] + 1
    contract_sha256 = sha256_bytes(canonical_json_bytes(contract))
    envelope = {
        "schema_version": 1,
        "analysis_id": analysis_id,
        "revision": revision_number,
        "contract_sha256": contract_sha256,
        "input_record_set_digest": input_record_set_digest(case.case_dir),
        "source_selection_approval_record_digest": _source_digest(
            source_selection_approval_record
        ),
        "contract": contract,
        "validation": {
            "approvable": validation.approvable,
            "issues": [asdict(item) for item in validation.issues],
        },
    }
    path = _intent_dir(case) / f"analysis-intent-r{revision_number:04d}.json"
    atomic_create_unique_artifact(
        path,
        canonical_json_bytes(envelope) + b"\n",
    )
    case.append(
        "INTENT_REVISION_CREATED",
        CaseState.INTENT_DRAFTED,
        {
            "revision": revision_number,
            "contract_sha256": contract_sha256,
            "revision_path": str(path),
            "prior_approval_invalidated": current is CaseState.INTENT_APPROVED,
        },
    )
    return IntentRevision(
        schema_version=1,
        analysis_id=analysis_id,
        revision=revision_number,
        contract_sha256=contract_sha256,
        input_record_set_digest=envelope["input_record_set_digest"],
        source_selection_approval_record_digest=(
            envelope["source_selection_approval_record_digest"]
        ),
        revision_path=path,
    )


def request_approval(
    case: CaseStore,
    revision: int,
    *,
    execution_profile_sha256: str,
    nonce: str | None = None,
    requested_at: str,
) -> ApprovalRequest:
    path = _intent_dir(case) / f"analysis-intent-r{revision:04d}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if case.replay()["harness"]["case_state"] != "INTENT_DRAFTED":
        raise ApprovalBindingError("case is not INTENT_DRAFTED")
    nonce_value = nonce or secrets.token_urlsafe(32)
    seed = {
        "analysis_id": value["analysis_id"],
        "revision": revision,
        "contract_sha256": value["contract_sha256"],
        "input_record_set_digest": value["input_record_set_digest"],
        "source_selection_approval_record_digest": (
            value["source_selection_approval_record_digest"]
        ),
        "execution_profile_sha256": execution_profile_sha256,
        "nonce": nonce_value,
        "requested_at": requested_at,
    }
    request_id = sha256_bytes(canonical_json_bytes(seed))
    request_path = _intent_dir(case) / f"approval-request-{request_id}.json"
    request = ApprovalRequest(
        schema_version=1,
        purpose="analysis-intent",
        request_id=request_id,
        **seed,
        request_path=request_path,
    )
    body = asdict(request)
    body.pop("request_path")
    validate_schema("approval-request", body)
    atomic_create_unique_artifact(
        request_path,
        canonical_json_bytes(body) + b"\n",
    )
    case.append(
        "INTENT_APPROVAL_REQUESTED",
        CaseState.INTENT_DRAFTED,
        {
            "request_id": request_id,
            "revision": revision,
            "process_started": False,
        },
    )
    return request


def _load_request(path: Path) -> ApprovalRequest:
    value = json.loads(path.read_text(encoding="utf-8"))
    validate_schema("approval-request", value)
    return ApprovalRequest(
        **value,
        request_path=path,
    )


def record_approval(
    case: CaseStore,
    request_path: Path,
    *,
    presented_nonce: str,
    approval_text: str,
    actor_kind: str,
    source_channel: str,
    approved_at: str,
    current_execution_profile_sha256: str,
    current_source_selection_approval_record: Path | None,
) -> ApprovalRecord:
    request = _load_request(request_path)
    record_path = _intent_dir(case) / f"approval-record-{request.request_id}.json"
    if record_path.exists():
        raise ApprovalBindingError("approval nonce was already used")
    if request.purpose != "analysis-intent":
        raise ApprovalBindingError("wrong approval purpose")
    if presented_nonce != request.nonce:
        raise ApprovalBindingError("approval nonce mismatch")
    if not approval_text.strip():
        raise ApprovalBindingError("approval text must not be blank")
    if actor_kind != "human":
        raise ApprovalBindingError("actor must be human")
    latest = _latest_revision(case)
    if latest is None or latest[0] != request.revision:
        raise ApprovalBindingError("contract revision drift")
    revision = json.loads(latest[1].read_text(encoding="utf-8"))
    if revision["contract_sha256"] != request.contract_sha256:
        raise ApprovalBindingError("contract hash drift")
    if (
        sha256_bytes(canonical_json_bytes(revision["contract"]))
        != request.contract_sha256
    ):
        raise ApprovalBindingError("contract bytes drift")
    if input_record_set_digest(case.case_dir) != request.input_record_set_digest:
        raise ApprovalBindingError("InputRecord set drift")
    if (
        _source_digest(current_source_selection_approval_record)
        != request.source_selection_approval_record_digest
    ):
        raise ApprovalBindingError("source-selection approval drift")
    if current_execution_profile_sha256 != request.execution_profile_sha256:
        raise ApprovalBindingError("execution profile drift")
    record = ApprovalRecord(
        schema_version=1,
        purpose="analysis-intent",
        request_id=request.request_id,
        analysis_id=request.analysis_id,
        revision=request.revision,
        contract_sha256=request.contract_sha256,
        input_record_set_digest=request.input_record_set_digest,
        source_selection_approval_record_digest=(
            request.source_selection_approval_record_digest
        ),
        execution_profile_sha256=request.execution_profile_sha256,
        nonce=presented_nonce,
        approval_text=approval_text,
        actor_kind=actor_kind,
        source_channel=source_channel,
        approved_at=approved_at,
        record_path=record_path,
    )
    body = asdict(record)
    body.pop("record_path")
    validate_schema("approval-record", body)
    try:
        atomic_create_unique_artifact(
            record_path,
            canonical_json_bytes(body) + b"\n",
        )
    except ArtifactCollisionError as error:
        raise ApprovalBindingError("approval nonce was already used") from error
    case.append(
        "INTENT_APPROVED",
        CaseState.INTENT_APPROVED,
        {
            "revision": request.revision,
            "request_id": request.request_id,
            "approval_record_sha256": sha256_file(record_path),
            "process_started": False,
        },
    )
    return record
```

- [ ] **Step 5: Add closed request and record schemas**

Create `schemas/approval-request.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/approval-request.schema.json",
  "description": "Audits binding but does not authenticate the speaker.",
  "type": "object",
  "required": [
    "schema_version", "purpose", "request_id", "analysis_id", "revision",
    "contract_sha256", "input_record_set_digest",
    "source_selection_approval_record_digest", "execution_profile_sha256",
    "nonce", "requested_at"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "purpose": {"const": "analysis-intent"},
    "request_id": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "analysis_id": {"type": "string", "minLength": 1},
    "revision": {"type": "integer", "minimum": 1},
    "contract_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "input_record_set_digest": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "source_selection_approval_record_digest": {
      "anyOf": [
        {"type": "null"},
        {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      ]
    },
    "execution_profile_sha256": {
      "type": "string", "pattern": "^[0-9A-F]{64}$"
    },
    "nonce": {"type": "string", "minLength": 16},
    "requested_at": {"type": "string", "format": "date-time"}
  },
  "additionalProperties": false
}
```

Create `schemas/approval-record.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/approval-record.schema.json",
  "description": "Human prose is required; the CLI cannot authenticate its author.",
  "type": "object",
  "required": [
    "schema_version", "purpose", "request_id", "analysis_id", "revision",
    "contract_sha256", "input_record_set_digest",
    "source_selection_approval_record_digest", "execution_profile_sha256",
    "nonce", "approval_text", "actor_kind", "source_channel", "approved_at"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "purpose": {"const": "analysis-intent"},
    "request_id": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "analysis_id": {"type": "string", "minLength": 1},
    "revision": {"type": "integer", "minimum": 1},
    "contract_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "input_record_set_digest": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "source_selection_approval_record_digest": {
      "anyOf": [
        {"type": "null"},
        {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      ]
    },
    "execution_profile_sha256": {
      "type": "string", "pattern": "^[0-9A-F]{64}$"
    },
    "nonce": {"type": "string", "minLength": 16},
    "approval_text": {"type": "string", "minLength": 1},
    "actor_kind": {"const": "human"},
    "source_channel": {"type": "string", "minLength": 1},
    "approved_at": {"type": "string", "format": "date-time"}
  },
  "additionalProperties": false
}
```

Add both schema paths to `EXPECTED_RESOURCES`.

- [ ] **Step 6: Document the approval trust boundary**

Append to `apps/febio_cae_harness/README.md`:

```markdown
## Approval trust boundary

The CLI verifies purpose, revision, hashes, nonce, nonblank approval text,
actor-kind metadata, channel, and timestamp. It cannot authenticate who typed
the prose. Codex must never invent or paraphrase human approval text; it records
only text explicitly supplied by the human for the exact request.
```

- [ ] **Step 7: Verify focused GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_approval.py `
  apps/febio_cae_harness/tests/contract/test_command_result_schema.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: `PASS` with exit code `0`; no attempt directory or model copy exists.

- [ ] **Step 8: Commit revision and approval**

```powershell
git add `
  apps/febio_cae_harness/src/febio_cae_harness/approval.py `
  apps/febio_cae_harness/src/febio_cae_harness/input_store.py `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/approval-request.schema.json `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/approval-record.schema.json `
  apps/febio_cae_harness/README.md `
  apps/febio_cae_harness/tests/unit/test_approval.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: bind analysis intent to human approval"
```

Expected: one commit and a clean `git status --short`.

## Task 14: Prove the Phase 1A stop-gate integration contract

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/phase1a.py`
- Create: `apps/febio_cae_harness/tests/integration/test_phase1a_core.py`

**Interfaces:**

- Consumes: `CaseStore`, `SourceExpectation`, blocker digest, Analysis Intent, execution-profile digest, source/intent nonces, and exact user approval fields.
- Produces: `Phase1ACore.prepare_source()`, `draft_intent()`, and `approve_intent()`; exact sources can reach `INPUT_INSPECTED`, unresolved authority stops at `WAITING_FOR_HUMAN`, and only bound Analysis Intent approval reaches `INTENT_APPROVED`.

- [ ] **Step 1: Add end-to-end core tests for exact and waiting paths**

Create
`apps/febio_cae_harness/tests/integration/test_phase1a_core.py`:

```python
from pathlib import Path

from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.phase1a import Phase1ACore
from febio_cae_harness.provenance import SourceExpectation
from test_intent_schema import complete_contract

FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "feb"
    / "complete-small.feb"
)


def exact_expectation(path: Path) -> SourceExpectation:
    return SourceExpectation(
        role="authoritative-feb",
        expected_sha256=sha256_file(path),
        expected_bytes=path.stat().st_size,
        preferred_path=path,
        search_roots=(path.parent,),
    )


def test_exact_source_to_intent_approved_is_replayable(tmp_path: Path) -> None:
    store = CaseStore.initialize(tmp_path / "case", "analysis-001")
    core = Phase1ACore(store)
    prepared = core.prepare_source(
        exact_expectation(FIXTURE),
        blocker_digest="A" * 64,
        source_selection_nonce="source-selection-nonce-001",
        occurred_at="2026-07-30T00:00:00Z",
    )
    assert prepared.status == "success"
    assert prepared.inspection is not None
    request = core.draft_intent(
        complete_contract(),
        prepared.inspection,
        source_selection_approval_record=None,
        execution_profile_sha256="E" * 64,
        approval_nonce="analysis-intent-nonce-001",
        requested_at="2026-07-30T00:01:00Z",
    )
    core.approve_intent(
        request.request_path,
        presented_nonce="analysis-intent-nonce-001",
        approval_text="I approve Analysis Intent revision 1 for execution.",
        actor_kind="human",
        source_channel="codex-desktop",
        approved_at="2026-07-30T00:02:00Z",
        current_execution_profile_sha256="E" * 64,
        current_source_selection_approval_record=None,
    )
    reopened = CaseStore.open(store.case_dir)
    assert reopened.replay()["harness"]["case_state"] == "INTENT_APPROVED"
    assert list((store.case_dir / "02_Model").iterdir()) == []
    assert {
        item.name
        for item in (store.case_dir / "90_Temporary").iterdir()
        if item.is_dir()
    } <= {"harness-lock-events"}


def test_alternate_source_stops_without_attempt_model_or_process(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    moved = external / "moved.feb"
    moved.write_bytes(FIXTURE.read_bytes())
    store = CaseStore.initialize(tmp_path / "case", "analysis-002")
    core = Phase1ACore(store)
    expectation = SourceExpectation(
        role="authoritative-feb",
        expected_sha256=sha256_file(moved),
        expected_bytes=moved.stat().st_size,
        preferred_path=external / "missing.feb",
        search_roots=(external,),
    )
    result = core.prepare_source(
        expectation,
        blocker_digest="B" * 64,
        source_selection_nonce="source-selection-nonce-002",
        occurred_at="2026-07-30T00:00:00Z",
    )
    assert result.status == "waiting_for_human"
    assert result.source_selection_request is not None
    assert store.replay()["harness"]["case_state"] == "WAITING_FOR_HUMAN"
    assert list((store.case_dir / "02_Model").iterdir()) == []
    assert {
        item.name
        for item in (store.case_dir / "90_Temporary").iterdir()
        if item.is_dir()
    } <= {"harness-lock-events"}
```

- [ ] **Step 2: Prove RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/integration/test_phase1a_core.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.phase1a'`.

- [ ] **Step 3: Implement the thin core orchestration**

Create `apps/febio_cae_harness/src/febio_cae_harness/phase1a.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .approval import (
    ApprovalRecord,
    ApprovalRequest,
    create_revision,
    record_approval,
    request_approval,
)
from .case_state import CaseState
from .case_store import CaseStore
from .feb_inspector import FebInspection, inspect_feb
from .input_store import ingest_create_new, input_record_set_digest
from .provenance import (
    SourceExpectation,
    SourceResolutionStatus,
    resolve_source,
)
from .source_approval import (
    SourceSelectionApprovalRequest,
    request_case_source_selection,
)


@dataclass(frozen=True)
class PreparationResult:
    status: str
    inspection: FebInspection | None
    source_selection_request: SourceSelectionApprovalRequest | None


class Phase1ACore:
    def __init__(self, store: CaseStore) -> None:
        self.store = store

    def prepare_source(
        self,
        expectation: SourceExpectation,
        *,
        blocker_digest: str,
        source_selection_nonce: str,
        occurred_at: str,
    ) -> PreparationResult:
        resolution = resolve_source(expectation)
        if resolution.status is SourceResolutionStatus.EXACT:
            selected = resolution.selected
            if selected is None:
                raise AssertionError("EXACT source lacks selected candidate")
            ingest_create_new(
                self.store.case_dir,
                selected.canonical_path,
                "authoritative-feb",
                resolution=resolution,
                acquired_at=occurred_at,
            )
            inspection = inspect_feb(selected.canonical_path)
            self.store.append(
                "INPUT_INSPECTED",
                CaseState.INPUT_INSPECTED,
                {
                    "input_record_set_digest": input_record_set_digest(
                        self.store.case_dir
                    ),
                    "source_sha256": selected.sha256,
                    "feb_domain_signature": inspection.domain_signature,
                    "invariant_signatures": inspection.invariant_signatures,
                    "process_started": False,
                },
            )
            return PreparationResult(
                status="success",
                inspection=inspection,
                source_selection_request=None,
            )
        if resolution.status in {
            SourceResolutionStatus.ALTERNATE_REQUIRES_APPROVAL,
            SourceResolutionStatus.AMBIGUOUS,
        }:
            request = request_case_source_selection(
                self.store,
                analysis_id=str(self.store.replay()["analysis_id"]),
                blocker_digest=blocker_digest,
                resolution=resolution,
                requested_at=occurred_at,
                nonce=source_selection_nonce,
            )
            return PreparationResult(
                status="waiting_for_human",
                inspection=None,
                source_selection_request=request,
            )
        current = CaseState(self.store.replay()["harness"]["case_state"])
        self.store.append(
            "SOURCE_AUTHORITY_BLOCKED",
            CaseState.WAITING_FOR_HUMAN,
            {
                "blocker_digest": blocker_digest,
                "resolution_status": resolution.status.value,
                "candidate_set_digest": resolution.candidate_set_digest,
                "resume_state": current.value,
                "process_started": False,
            },
        )
        return PreparationResult(
            status="waiting_for_human",
            inspection=None,
            source_selection_request=None,
        )

    def draft_intent(
        self,
        contract: dict[str, object],
        inspection: FebInspection,
        *,
        source_selection_approval_record: Path | None,
        execution_profile_sha256: str,
        approval_nonce: str,
        requested_at: str,
    ) -> ApprovalRequest:
        revision = create_revision(
            self.store,
            contract,
            inspection={
                "references": [
                    {
                        "owner_xml_path": item.owner_xml_path,
                        "target": item.target,
                        "resolved": item.resolved,
                    }
                    for item in inspection.references
                ]
            },
            source_selection_approval_record=(
                source_selection_approval_record
            ),
        )
        return request_approval(
            self.store,
            revision.revision,
            execution_profile_sha256=execution_profile_sha256,
            nonce=approval_nonce,
            requested_at=requested_at,
        )

    def approve_intent(
        self,
        request_path: Path,
        **approval_fields: object,
    ) -> ApprovalRecord:
        return record_approval(
            self.store,
            request_path,
            **approval_fields,
        )
```

- [ ] **Step 4: Verify focused GREEN and replay after reopen**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/integration/test_phase1a_core.py -q
```

Expected: `2 passed`; exact input reaches `INTENT_APPROVED`, alternate input
stops at `WAITING_FOR_HUMAN`, and neither path creates a model, attempt, or
process.

- [ ] **Step 5: Run the complete Phase 1A regression**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest apps/febio_cae_harness/tests -q
if ($LASTEXITCODE -ne 0) {
    throw "Phase 1A suite failed"
}
& .\.venv\Scripts\python.exe -m pytest apps/febio_gmsh_launcher/tests -q
if ($LASTEXITCODE -ne 0) {
    throw "Existing launcher regression failed"
}
git status --short
```

Expected: both pytest commands exit `0`; status lists only
`test_phase1a_core.py` and `phase1a.py`.

- [ ] **Step 6: Commit the integration checkpoint**

```powershell
git add `
  apps/febio_cae_harness/src/febio_cae_harness/phase1a.py `
  apps/febio_cae_harness/tests/integration/test_phase1a_core.py
git commit -m "test: prove phase1a intent approval stop gates"
git status --short --branch
```

Expected: one commit and:

```text
## codex/febio-cae-harness-phase1
```

## Phase 1A Handoff Contract

Later plans must import these exact APIs rather than a private helper:

| Consumer need | Canonical Phase 1A API or field |
|---|---|
| Approved plan-suite evidence | `schema_version=1`, `plan_commit`, and `files` with six `{path, sha256, bytes, git_blob}` entries in `docs/superpowers/plans/2026-07-30-febio-cae-harness-phase1-approved-suite.json` |
| Multi-operation case mutation | `with case.locked() as transaction:` |
| Read while lock is held | `transaction.replay()` |
| Append while lock is held | `transaction.append(event_type, to_state, payload)` |
| One event operation | `case.append(event_type, to_state, payload)` |
| Legacy adoption | `CaseStore.adopt_existing(case_dir, expected_manifest_sha256=manifest_sha256, preexisting_inventory_json=inventory_path, expected_preexisting_inventory_sha256=inventory_sha256)` |
| Reload bound pre-existing inventory | `case.load_preexisting_inventory()` |
| Path FEB inspection | `inspect_feb(path, excluded_domains=())` |
| In-memory before/after inspection | `inspect_feb_bytes(data, source_name=source_name, excluded_domains=())` |
| Patcher invariant comparison | `inspection.invariant_signatures[kind]` for the seven closed kinds |
| Source request | `load_source_expectation(path)` / `SourceExpectation.from_payload(value)`, then `resolve_source()` |
| Input provenance set binding | `input_record_set_digest(case_dir)` |
| CLI evidence lookup | `EvidenceRecord.kind` and `EvidenceRecord.data` |

The seven invariant kinds are exactly `domain`, `material`,
`reference_closure`, `load`, `boundary`, `contact`, and `output`.
`CommandResult.status` is exactly lowercase `success`, `waiting_for_human`, or
`error`; `CommandResult.evidence` is a tuple serialized as a JSON list of
closed `{kind, data}` objects.

The Setup Gate history is a three-commit contract:
`parent(ApprovedSuiteCommit) == PlanCommit` and
`parent(BoundaryApprovalCommit) == ApprovedSuiteCommit`. The six plans and the
approved-suite manifest's `plan_commit`/`files` hashes belong to `PlanCommit`;
that manifest is loaded from `ApprovedSuiteCommit` and never hashes itself.
The only file added by `BoundaryApprovalCommit` is the hash-only
boundary-review manifest that binds the human-reviewed external
baseline/preview/review record.

The source-expectation payload fields are exactly `schema_version`, `role`,
`preferred_path`, `expected_sha256`, `expected_bytes`, `search_roots`,
`prohibited_candidates`, `lineage_evidence`, `reference_results`, and
`lineage`. `lineage_evidence` has exactly four `{kind, canonical_path, sha256,
bytes}` pointers; `reference_results` has exactly two such pointers plus
`reuse=false`; `lineage` contains `branch` and `commit`. These are provenance
pointers only and never authorize copying baseline LOG or XPLT bytes.

Phase 1A ends at `INTENT_APPROVED`. It does not adopt a FEB into `02_Model`,
create an attempt, compile a model, start FEBio, parse XPLT, promote results, or
claim engineering/manufacturing validity.

## Task 8: Resolve external sources and persist immutable provenance

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/provenance.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/input_store.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/input-record.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/source-expectation.schema.json`
- Create: `apps/febio_cae_harness/tests/unit/test_provenance.py`
- Create: `apps/febio_cae_harness/tests/integration/test_input_store.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**

- Consumes: `SourceExpectation`, preferred path, declared search roots, expected bytes/SHA-256, structured prohibited predecessors, four lineage-evidence pointers, two non-reusable reference-result pointers, lineage branch/commit, and a case `01_Input` directory.
- Produces: schema-validating `SourceExpectation.from_payload()` / `load_source_expectation()`, deterministic `SourceResolution` with a complete candidate-set digest, and create-new `InputRecord` JSON containing the complete expectation. Evidence/result pointers persist path/hash/bytes only; it never copies external FEB/STEP/LOG/XPLT bytes and never writes `02_Model`.

- [ ] **Step 1: Add exact-path, fallback, ambiguity, and provenance tests**

Create `apps/febio_cae_harness/tests/unit/test_provenance.py`:

```python
import json
from pathlib import Path

from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.provenance import (
    ExternalEvidencePointer,
    ProhibitedCandidate,
    ReferenceResultPointer,
    SourceExpectation,
    SourceResolutionStatus,
    load_source_expectation,
    resolve_source,
)
from febio_cae_harness.schema import validate_schema


def expectation(
    source: Path,
    root: Path,
    *,
    preferred: Path | None,
    prohibited: tuple[str, ...] = (),
) -> SourceExpectation:
    return SourceExpectation(
        role="baseline-feb",
        expected_sha256=sha256_file(source),
        expected_bytes=source.stat().st_size,
        preferred_path=preferred,
        search_roots=(root,),
        prohibited_sha256=prohibited,
    )


def test_preferred_path_hash_and_size_resolve_exactly(tmp_path: Path) -> None:
    source = tmp_path / "source.feb"
    source.write_bytes(b"current")
    result = resolve_source(expectation(source, tmp_path, preferred=source))
    assert result.status is SourceResolutionStatus.EXACT
    assert result.selected is not None
    assert result.selected.canonical_path == source.resolve()
    assert result.selected.sha256 == sha256_file(source)
    assert result.selected.bytes == 7


def test_one_hash_only_fallback_requires_selection_approval(tmp_path: Path) -> None:
    source = tmp_path / "moved" / "source.feb"
    source.parent.mkdir()
    source.write_bytes(b"current")
    missing = tmp_path / "old" / "source.feb"
    result = resolve_source(expectation(source, tmp_path, preferred=missing))
    assert result.status is SourceResolutionStatus.ALTERNATE_REQUIRES_APPROVAL
    assert result.selected is None
    assert [item.canonical_path for item in result.candidates_matching_expected] == [
        source.resolve()
    ]


def test_multiple_hash_matches_are_ambiguous(tmp_path: Path) -> None:
    first = tmp_path / "one.feb"
    second = tmp_path / "nested" / "two.feb"
    second.parent.mkdir()
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    result = resolve_source(expectation(first, tmp_path, preferred=None))
    assert result.status is SourceResolutionStatus.AMBIGUOUS
    assert len(result.candidates_matching_expected) == 2


def test_prohibited_predecessor_is_not_selectable(tmp_path: Path) -> None:
    predecessor = tmp_path / "old.feb"
    predecessor.write_bytes(b"old")
    expectation_value = SourceExpectation(
        role="baseline-feb",
        expected_sha256="F" * 64,
        expected_bytes=3,
        preferred_path=predecessor,
        search_roots=(tmp_path,),
        prohibited_sha256=(sha256_file(predecessor),),
    )
    result = resolve_source(expectation_value)
    assert result.status is SourceResolutionStatus.PROHIBITED_PREDECESSOR
    assert result.selected is None


def test_moved_copy_of_prohibited_hash_is_not_approvable(tmp_path: Path) -> None:
    moved = tmp_path / "moved" / "old.feb"
    moved.parent.mkdir()
    moved.write_bytes(b"old")
    digest = sha256_file(moved)
    result = resolve_source(SourceExpectation(
        role="baseline-feb",
        expected_sha256=digest,
        expected_bytes=moved.stat().st_size,
        preferred_path=tmp_path / "missing" / "old.feb",
        search_roots=(tmp_path,),
        prohibited_sha256=(digest,),
    ))
    assert result.status is SourceResolutionStatus.PROHIBITED_PREDECESSOR
    assert result.selected is None


def test_candidate_digest_is_independent_of_search_order(tmp_path: Path) -> None:
    first_root = tmp_path / "a"
    second_root = tmp_path / "b"
    first_root.mkdir()
    second_root.mkdir()
    source = first_root / "source.feb"
    source.write_bytes(b"same")
    (second_root / "copy.feb").write_bytes(b"same")
    forward = SourceExpectation(
        "baseline-feb",
        sha256_file(source),
        4,
        None,
        (first_root, second_root),
    )
    reverse = SourceExpectation(
        "baseline-feb",
        sha256_file(source),
        4,
        None,
        (second_root, first_root),
    )
    assert resolve_source(forward).candidate_set_digest == resolve_source(
        reverse
    ).candidate_set_digest


def test_phase1e_source_request_has_closed_lineage_contract(tmp_path: Path) -> None:
    authoritative = tmp_path / "authoritative.feb"
    authoritative.write_bytes(b"authoritative")
    evidence = []
    for index in range(4):
        path = tmp_path / f"lineage-{index}.json"
        path.write_bytes(f"evidence-{index}".encode())
        evidence.append(ExternalEvidencePointer(
            kind=f"lineage_{index}",
            canonical_path=path,
            sha256=sha256_file(path),
            bytes=path.stat().st_size,
        ))
    results = []
    for index, suffix in enumerate(("log", "xplt")):
        path = tmp_path / f"reference-{index}.{suffix}"
        path.write_bytes(f"result-{index}".encode())
        results.append(ReferenceResultPointer(
            kind=f"reference_{suffix}",
            canonical_path=path,
            sha256=sha256_file(path),
            bytes=path.stat().st_size,
            reuse=False,
        ))
    expectation_value = SourceExpectation(
        role="authoritative-feb",
        expected_sha256=sha256_file(authoritative),
        expected_bytes=authoritative.stat().st_size,
        preferred_path=authoritative,
        search_roots=(tmp_path,),
        prohibited_candidates=(ProhibitedCandidate(
            sha256="A" * 64,
            reason="listed predecessor",
        ),),
        lineage_evidence=tuple(evidence),
        reference_results=tuple(results),
        lineage_branch="codex/febio-cae-harness-phase1",
        lineage_commit="a" * 40,
    )
    payload = expectation_value.to_payload()
    validate_schema("source-expectation", payload)
    assert payload["schema_version"] == 1
    assert len(payload["lineage_evidence"]) == 4
    assert len(payload["reference_results"]) == 2
    assert all(item["reuse"] is False for item in payload["reference_results"])
    request_path = tmp_path / "source-expectation.json"
    request_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    loaded = load_source_expectation(request_path)
    assert loaded.to_payload() == payload
    assert loaded.preferred_path == authoritative.resolve()
```

Create `apps/febio_cae_harness/tests/integration/test_input_store.py`:

```python
import json
from pathlib import Path

import pytest

from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.input_store import ingest_create_new
from febio_cae_harness.jsonio import ArtifactCollisionError
from febio_cae_harness.provenance import SourceExpectation, resolve_source


def test_record_is_create_new_and_does_not_copy_source_bytes(
    tmp_path: Path,
) -> None:
    case = tmp_path / "case"
    (case / "01_Input").mkdir(parents=True)
    (case / "02_Model").mkdir()
    source = tmp_path / "external" / "baseline.feb"
    source.parent.mkdir()
    source.write_bytes(b"<febio_spec/>")
    resolution = resolve_source(SourceExpectation(
        role="baseline-feb",
        expected_sha256=sha256_file(source),
        expected_bytes=source.stat().st_size,
        preferred_path=source,
        search_roots=(source.parent,),
    ))
    record = ingest_create_new(
        case,
        source,
        "external-source-record",
        resolution=resolution,
        acquired_at="2026-07-30T00:00:00Z",
    )
    persisted = json.loads(record.record_path.read_text(encoding="utf-8"))
    assert persisted["canonical_path"] == str(source.resolve())
    assert persisted["sha256"] == sha256_file(source)
    assert persisted["copied_path_evidence"] is None
    assert list((case / "02_Model").iterdir()) == []
    assert all(item.suffix == ".json" for item in (case / "01_Input").iterdir())


def test_same_record_is_idempotent_but_different_bytes_collide(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    case = tmp_path / "case"
    (case / "01_Input").mkdir(parents=True)
    source = tmp_path / "source.feb"
    source.write_bytes(b"stable")
    first = ingest_create_new(
        case,
        source,
        "baseline-result-reference",
        acquired_at="2026-07-30T00:00:00Z",
    )
    second = ingest_create_new(
        case,
        source,
        "baseline-result-reference",
        acquired_at="2026-07-30T00:00:00Z",
    )
    assert first.record_path == second.record_path
    monkeypatch.setattr(
        "febio_cae_harness.input_store._source_snapshot",
        lambda path: (path.stat(), "A" * 64),
    )
    with pytest.raises(ArtifactCollisionError):
        ingest_create_new(
            case,
            source,
            "baseline-result-reference",
            acquired_at="2026-07-30T00:00:00Z",
        )
```

- [ ] **Step 2: Prove RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_provenance.py `
  apps/febio_cae_harness/tests/integration/test_input_store.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.provenance'`.

- [ ] **Step 3: Implement deterministic source resolution**

Create `apps/febio_cae_harness/src/febio_cae_harness/provenance.py`:

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .schema import validate_schema


class SourceResolutionStatus(StrEnum):
    EXACT = "EXACT"
    ALTERNATE_REQUIRES_APPROVAL = "ALTERNATE_REQUIRES_APPROVAL"
    AMBIGUOUS = "AMBIGUOUS"
    MISSING = "MISSING"
    PROHIBITED_PREDECESSOR = "PROHIBITED_PREDECESSOR"


@dataclass(frozen=True)
class ExternalEvidencePointer:
    kind: str
    canonical_path: Path
    sha256: str
    bytes: int

    def to_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "canonical_path": str(self.canonical_path.resolve(strict=True)),
            "sha256": self.sha256,
            "bytes": self.bytes,
        }


@dataclass(frozen=True)
class ReferenceResultPointer(ExternalEvidencePointer):
    reuse: bool = False

    def to_payload(self) -> dict[str, object]:
        return {**super().to_payload(), "reuse": self.reuse}


@dataclass(frozen=True)
class ProhibitedCandidate:
    sha256: str
    reason: str
    canonical_path: Path | None = None
    bytes: int | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "sha256": self.sha256,
            "reason": self.reason,
            "canonical_path": (
                None
                if self.canonical_path is None
                else str(self.canonical_path.resolve(strict=True))
            ),
            "bytes": self.bytes,
        }


@dataclass(frozen=True)
class SourceExpectation:
    role: str
    expected_sha256: str
    expected_bytes: int
    preferred_path: Path | None
    search_roots: tuple[Path, ...]
    prohibited_sha256: tuple[str, ...] = ()
    prohibited_candidates: tuple[ProhibitedCandidate, ...] = ()
    lineage_evidence: tuple[ExternalEvidencePointer, ...] = ()
    reference_results: tuple[ReferenceResultPointer, ...] = ()
    lineage_branch: str | None = None
    lineage_commit: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "role": self.role,
            "preferred_path": (
                None
                if self.preferred_path is None
                else str(self.preferred_path.resolve(strict=False))
            ),
            "expected_sha256": self.expected_sha256,
            "expected_bytes": self.expected_bytes,
            "search_roots": [
                str(root.resolve(strict=True)) for root in self.search_roots
            ],
            "prohibited_candidates": [
                item.to_payload() for item in self.prohibited_candidates
            ] + [
                {
                    "sha256": digest,
                    "reason": "prohibited predecessor hash",
                    "canonical_path": None,
                    "bytes": None,
                }
                for digest in self.prohibited_sha256
            ],
            "lineage_evidence": [
                item.to_payload() for item in self.lineage_evidence
            ],
            "reference_results": [
                item.to_payload() for item in self.reference_results
            ],
            "lineage": {
                "branch": self.lineage_branch,
                "commit": self.lineage_commit,
            },
        }

    @classmethod
    def from_payload(cls, value: dict[str, object]) -> "SourceExpectation":
        validate_schema("source-expectation", value)
        lineage = value["lineage"]
        if not isinstance(lineage, dict):
            raise ValueError("source expectation lineage must be an object")
        return cls(
            role=str(value["role"]),
            expected_sha256=str(value["expected_sha256"]),
            expected_bytes=int(value["expected_bytes"]),
            preferred_path=(
                None
                if value["preferred_path"] is None
                else Path(str(value["preferred_path"]))
            ),
            search_roots=tuple(
                Path(str(item)) for item in value["search_roots"]
            ),
            prohibited_candidates=tuple(
                ProhibitedCandidate(
                    sha256=str(item["sha256"]),
                    reason=str(item["reason"]),
                    canonical_path=(
                        None
                        if item["canonical_path"] is None
                        else Path(str(item["canonical_path"]))
                    ),
                    bytes=(
                        None
                        if item["bytes"] is None
                        else int(item["bytes"])
                    ),
                )
                for item in value["prohibited_candidates"]
            ),
            lineage_evidence=tuple(
                ExternalEvidencePointer(
                    kind=str(item["kind"]),
                    canonical_path=Path(str(item["canonical_path"])),
                    sha256=str(item["sha256"]),
                    bytes=int(item["bytes"]),
                )
                for item in value["lineage_evidence"]
            ),
            reference_results=tuple(
                ReferenceResultPointer(
                    kind=str(item["kind"]),
                    canonical_path=Path(str(item["canonical_path"])),
                    sha256=str(item["sha256"]),
                    bytes=int(item["bytes"]),
                    reuse=bool(item["reuse"]),
                )
                for item in value["reference_results"]
            ),
            lineage_branch=str(lineage["branch"]),
            lineage_commit=str(lineage["commit"]),
        )


def load_source_expectation(path: Path) -> SourceExpectation:
    canonical = path.resolve(strict=True)
    try:
        value = json.loads(canonical.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid source-expectation JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("source-expectation JSON must be an object")
    return SourceExpectation.from_payload(value)


@dataclass(frozen=True)
class SourceCandidate:
    canonical_path: Path
    bytes: int
    sha256: str
    modified_ns: int

    def digest_value(self) -> dict[str, object]:
        return {
            "canonical_path": str(self.canonical_path),
            "bytes": self.bytes,
            "sha256": self.sha256,
            "modified_ns": self.modified_ns,
        }


@dataclass(frozen=True)
class SourceResolution:
    expectation: SourceExpectation
    status: SourceResolutionStatus
    selected: SourceCandidate | None
    discovered_candidates: tuple[SourceCandidate, ...]
    candidates_matching_expected: tuple[SourceCandidate, ...]
    candidate_set_digest: str
    selection_reason: str


def _candidate(path: Path) -> SourceCandidate:
    canonical = path.resolve(strict=True)
    stat = canonical.stat()
    return SourceCandidate(
        canonical_path=canonical,
        bytes=stat.st_size,
        sha256=sha256_file(canonical),
        modified_ns=stat.st_mtime_ns,
    )


def resolve_source(expectation: SourceExpectation) -> SourceResolution:
    if len(expectation.expected_sha256) != 64:
        raise ValueError("expected_sha256 must contain 64 hex characters")
    paths: set[Path] = set()
    if expectation.preferred_path is not None and expectation.preferred_path.is_file():
        paths.add(expectation.preferred_path.resolve())
    for root in expectation.search_roots:
        canonical_root = root.resolve(strict=True)
        if not canonical_root.is_dir():
            raise ValueError(f"search root is not a directory: {canonical_root}")
        paths.update(
            path.resolve()
            for path in canonical_root.rglob("*")
            if path.is_file()
        )
    discovered = tuple(
        sorted(
            (_candidate(path) for path in paths),
            key=lambda item: str(item.canonical_path).casefold(),
        )
    )
    digest = sha256_bytes(canonical_json_bytes([
        item.digest_value() for item in discovered
    ]))
    prohibited = set(expectation.prohibited_sha256) | {
        item.sha256 for item in expectation.prohibited_candidates
    }
    preferred_candidate = next(
        (
            item
            for item in discovered
            if expectation.preferred_path is not None
            and item.canonical_path == expectation.preferred_path.resolve()
        ),
        None,
    )
    matches = tuple(
        item
        for item in discovered
        if item.sha256 == expectation.expected_sha256
        and item.bytes == expectation.expected_bytes
    )
    if expectation.expected_sha256 in prohibited:
        status = SourceResolutionStatus.PROHIBITED_PREDECESSOR
        selected = None
        reason = "expected authority hash is a prohibited predecessor"
    elif (
        preferred_candidate is not None
        and preferred_candidate.sha256 in prohibited
    ):
        status = SourceResolutionStatus.PROHIBITED_PREDECESSOR
        selected = None
        reason = "preferred path is a prohibited predecessor"
    elif preferred_candidate is not None and preferred_candidate in matches:
        status = SourceResolutionStatus.EXACT
        selected = preferred_candidate
        reason = "preferred canonical path, SHA-256, and byte count match"
    elif len(matches) == 1:
        status = SourceResolutionStatus.ALTERNATE_REQUIRES_APPROVAL
        selected = None
        reason = "one alternate hash match requires source-selection approval"
    elif len(matches) > 1:
        status = SourceResolutionStatus.AMBIGUOUS
        selected = None
        reason = "multiple candidates match expected hash and byte count"
    else:
        status = SourceResolutionStatus.MISSING
        selected = None
        reason = "no candidate matches expected hash and byte count"
    return SourceResolution(
        expectation=expectation,
        status=status,
        selected=selected,
        discovered_candidates=discovered,
        candidates_matching_expected=matches,
        candidate_set_digest=digest,
        selection_reason=reason,
    )
```

- [ ] **Step 4: Implement create-new input records**

Create `apps/febio_cae_harness/src/febio_cae_harness/input_store.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .jsonio import atomic_create_unique_artifact
from .provenance import SourceCandidate, SourceResolution

DestinationRole = Literal[
    "authoritative-feb",
    "external-source-record",
    "baseline-result-reference",
]


@dataclass(frozen=True)
class InputRecord:
    record_id: str
    record_path: Path
    canonical_path: Path
    sha256: str
    bytes: int
    destination_role: DestinationRole


def _source_snapshot(path: Path):
    stat = path.stat()
    return stat, sha256_file(path)


def ingest_create_new(
    case_dir: Path,
    source: Path,
    destination_role: DestinationRole,
    *,
    resolution: SourceResolution | None = None,
    acquired_at: str | None = None,
) -> InputRecord:
    canonical = source.resolve(strict=True)
    before, digest = _source_snapshot(canonical)
    after, confirmed_digest = _source_snapshot(canonical)
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or digest != confirmed_digest
    ):
        raise OSError("SOURCE_MUTATED_DURING_ACQUISITION")
    acquired = acquired_at or datetime.now(UTC).isoformat().replace(
        "+00:00",
        "Z",
    )
    candidates = (
        []
        if resolution is None
        else [
            candidate.digest_value()
            for candidate in resolution.discovered_candidates
        ]
    )
    reason = (
        "direct external reference"
        if resolution is None
        else resolution.selection_reason
    )
    body = {
        "schema_version": 1,
        "destination_role": destination_role,
        "canonical_path": str(canonical),
        "observed_modified_ns": after.st_mtime_ns,
        "bytes": after.st_size,
        "sha256": confirmed_digest,
        "acquired_at": acquired,
        "discovery_candidates": candidates,
        "candidate_set_digest": (
            None if resolution is None else resolution.candidate_set_digest
        ),
        "source_expectation": (
            None
            if resolution is None
            else resolution.expectation.to_payload()
        ),
        "selection_reason": reason,
        "copied_path_evidence": None,
    }
    record_id = sha256_bytes(canonical_json_bytes(body))
    persisted = {"record_id": record_id, **body}
    path = (
        case_dir
        / "01_Input"
        / f"{destination_role}-{record_id}.json"
    )
    atomic_create_unique_artifact(
        path,
        canonical_json_bytes(persisted) + b"\n",
    )
    reread = json.loads(path.read_text(encoding="utf-8"))
    if reread != persisted:
        raise OSError("input record verification failed")
    return InputRecord(
        record_id=record_id,
        record_path=path,
        canonical_path=canonical,
        sha256=confirmed_digest,
        bytes=after.st_size,
        destination_role=destination_role,
    )
```

- [ ] **Step 5: Add the input-record schema and resource entry**

Create
`apps/febio_cae_harness/src/febio_cae_harness/schemas/input-record.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/input-record.schema.json",
  "type": "object",
  "required": [
    "record_id",
    "schema_version",
    "destination_role",
    "canonical_path",
    "observed_modified_ns",
    "bytes",
    "sha256",
    "acquired_at",
    "discovery_candidates",
    "candidate_set_digest",
    "selection_reason",
    "source_expectation",
    "copied_path_evidence"
  ],
  "properties": {
    "record_id": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "schema_version": {"const": 1},
    "destination_role": {
      "enum": [
        "authoritative-feb",
        "external-source-record",
        "baseline-result-reference"
      ]
    },
    "canonical_path": {"type": "string", "minLength": 1},
    "observed_modified_ns": {"type": "integer", "minimum": 0},
    "bytes": {"type": "integer", "minimum": 0},
    "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "acquired_at": {"type": "string", "format": "date-time"},
    "discovery_candidates": {"type": "array", "items": {"type": "object"}},
    "candidate_set_digest": {
      "anyOf": [
        {"type": "null"},
        {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      ]
    },
    "selection_reason": {"type": "string", "minLength": 1},
    "source_expectation": {"type": ["object", "null"]},
    "copied_path_evidence": {"type": "null"}
  },
  "additionalProperties": false
}
```

Create
`apps/febio_cae_harness/src/febio_cae_harness/schemas/source-expectation.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/source-expectation.schema.json",
  "type": "object",
  "required": [
    "schema_version", "role", "preferred_path", "expected_sha256", "expected_bytes",
    "search_roots", "prohibited_candidates", "lineage_evidence",
    "reference_results", "lineage"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "role": {"type": "string", "minLength": 1},
    "preferred_path": {"type": ["string", "null"]},
    "expected_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "expected_bytes": {"type": "integer", "minimum": 0},
    "search_roots": {
      "type": "array", "minItems": 1,
      "items": {"type": "string", "minLength": 1}
    },
    "prohibited_candidates": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["sha256", "reason", "canonical_path", "bytes"],
        "properties": {
          "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
          "reason": {"type": "string", "minLength": 1},
          "canonical_path": {"type": ["string", "null"]},
          "bytes": {"type": ["integer", "null"], "minimum": 0}
        },
        "additionalProperties": false
      }
    },
    "lineage_evidence": {
      "type": "array", "minItems": 4, "maxItems": 4,
      "items": {"$ref": "#/$defs/externalPointer"}
    },
    "reference_results": {
      "type": "array", "minItems": 2, "maxItems": 2,
      "items": {"$ref": "#/$defs/referenceResultPointer"}
    },
    "lineage": {
      "type": "object",
      "required": ["branch", "commit"],
      "properties": {
        "branch": {"type": "string", "minLength": 1},
        "commit": {"type": "string", "pattern": "^[0-9a-fA-F]{40,64}$"}
      },
      "additionalProperties": false
    }
  },
  "$defs": {
    "externalPointer": {
      "type": "object",
      "required": ["kind", "canonical_path", "sha256", "bytes"],
      "properties": {
        "kind": {"type": "string", "minLength": 1},
        "canonical_path": {"type": "string", "minLength": 1},
        "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "bytes": {"type": "integer", "minimum": 0}
      },
      "additionalProperties": false
    },
    "referenceResultPointer": {
      "type": "object",
      "required": ["kind", "canonical_path", "sha256", "bytes", "reuse"],
      "properties": {
        "kind": {"type": "string", "minLength": 1},
        "canonical_path": {"type": "string", "minLength": 1},
        "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
        "bytes": {"type": "integer", "minimum": 0},
        "reuse": {"const": false}
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": false
}
```

Set `EXPECTED_RESOURCES` to:

```python
EXPECTED_RESOURCES = {
    "schemas/case-event.schema.json",
    "schemas/case-manifest.schema.json",
    "schemas/command-result.schema.json",
    "schemas/input-record.schema.json",
    "schemas/preexisting-inventory.schema.json",
    "schemas/source-expectation.schema.json",
}
```

- [ ] **Step 6: Verify focused GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_provenance.py `
  apps/febio_cae_harness/tests/integration/test_input_store.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: `PASS` with exit code `0`; `02_Model` remains empty.

- [ ] **Step 7: Commit provenance**

```powershell
git add `
  apps/febio_cae_harness/src/febio_cae_harness/provenance.py `
  apps/febio_cae_harness/src/febio_cae_harness/input_store.py `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/input-record.schema.json `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/source-expectation.schema.json `
  apps/febio_cae_harness/tests/unit/test_provenance.py `
  apps/febio_cae_harness/tests/integration/test_input_store.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: add immutable CAE input provenance"
```

Expected: one commit and a clean `git status --short`.

## Task 9: Gate alternate or ambiguous sources with separate approval

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/source_approval.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/source-selection-approval-request.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/source-selection-approval-record.schema.json`
- Create: `apps/febio_cae_harness/tests/unit/test_source_approval.py`
- Create: `apps/febio_cae_harness/tests/integration/test_source_selection_gate.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**

- Consumes: `analysis_id`, unresolved `SourceResolution`, blocker digest, complete candidate-set digest, exact selected path/hash/bytes, one-time nonce, human text, actor kind, channel, and UTC time.
- Produces: distinct create-new `SourceSelectionApprovalRequest` and
  `SourceSelectionApprovalRecord`; request moves the case to
  `WAITING_FOR_HUMAN`, approval resumes only the saved state, and immutable
  `SOURCE_SELECTION_APPROVED` binds the record path/SHA-256, candidate-set
  digest, and selected candidate. Neither object can satisfy Analysis Intent
  approval.

- [ ] **Step 1: Add binding, replay, and no-side-effect tests**

Create `apps/febio_cae_harness/tests/unit/test_source_approval.py`:

```python
from pathlib import Path

import pytest

from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.provenance import SourceExpectation, resolve_source
from febio_cae_harness.source_approval import (
    ApprovalBindingError,
    approve_source_selection,
    create_source_selection_request,
)


def unresolved(tmp_path: Path):
    selected = tmp_path / "moved.feb"
    selected.write_bytes(b"current")
    resolution = resolve_source(SourceExpectation(
        role="baseline-feb",
        expected_sha256=sha256_file(selected),
        expected_bytes=selected.stat().st_size,
        preferred_path=tmp_path / "missing.feb",
        search_roots=(tmp_path,),
    ))
    return selected, resolution


def test_record_binds_every_authority_field(tmp_path: Path) -> None:
    case = tmp_path / "case"
    (case / "01_Input").mkdir(parents=True)
    selected, resolution = unresolved(tmp_path)
    request = create_source_selection_request(
        case,
        analysis_id="analysis-001",
        blocker_digest="A" * 64,
        resolution=resolution,
        requested_at="2026-07-30T00:00:00Z",
        nonce="nonce-source-001",
    )
    record = approve_source_selection(
        request.request_path,
        selected_path=selected,
        presented_nonce="nonce-source-001",
        approval_text="Use the moved FEB with the verified hash.",
        actor_kind="human",
        source_channel="codex-desktop",
        approved_at="2026-07-30T00:01:00Z",
    )
    assert record.analysis_id == "analysis-001"
    assert record.blocker_digest == "A" * 64
    assert record.candidate_set_digest == resolution.candidate_set_digest
    assert record.selected_sha256 == sha256_file(selected)
    assert record.purpose == "SOURCE_SELECTION"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("nonce", "wrong", "nonce"),
        ("path", "not-a-candidate", "candidate"),
        ("text", " ", "approval text"),
        ("actor", "agent", "actor"),
    ],
)
def test_drift_or_nonhuman_approval_fails_closed(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    case = tmp_path / "case"
    (case / "01_Input").mkdir(parents=True)
    selected, resolution = unresolved(tmp_path)
    request = create_source_selection_request(
        case,
        analysis_id="analysis-001",
        blocker_digest="A" * 64,
        resolution=resolution,
        requested_at="2026-07-30T00:00:00Z",
        nonce="nonce-source-001",
    )
    alternate = tmp_path / "not-a-candidate.feb"
    alternate.write_bytes(b"other")
    with pytest.raises(ApprovalBindingError, match=message):
        approve_source_selection(
            request.request_path,
            selected_path=alternate if field == "path" else selected,
            presented_nonce=value if field == "nonce" else "nonce-source-001",
            approval_text=value if field == "text" else "Approved.",
            actor_kind=value if field == "actor" else "human",
            source_channel="codex-desktop",
            approved_at="2026-07-30T00:01:00Z",
        )


def test_nonce_is_one_time(tmp_path: Path) -> None:
    case = tmp_path / "case"
    (case / "01_Input").mkdir(parents=True)
    selected, resolution = unresolved(tmp_path)
    request = create_source_selection_request(
        case,
        analysis_id="analysis-001",
        blocker_digest="A" * 64,
        resolution=resolution,
        requested_at="2026-07-30T00:00:00Z",
        nonce="nonce-source-001",
    )
    approve_source_selection(
        request.request_path,
        selected_path=selected,
        presented_nonce="nonce-source-001",
        approval_text="Approved.",
        actor_kind="human",
        source_channel="codex-desktop",
        approved_at="2026-07-30T00:01:00Z",
    )
    with pytest.raises(ApprovalBindingError, match="already used"):
        approve_source_selection(
            request.request_path,
            selected_path=selected,
            presented_nonce="nonce-source-001",
            approval_text="Approved again.",
            actor_kind="human",
            source_channel="codex-desktop",
            approved_at="2026-07-30T00:02:00Z",
        )
```

Create
`apps/febio_cae_harness/tests/integration/test_source_selection_gate.py`:

```python
from pathlib import Path

from febio_cae_harness.case_state import CaseState
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.events import read_event_log
from febio_cae_harness.hashing import sha256_file
from febio_cae_harness.provenance import SourceExpectation, resolve_source
from febio_cae_harness.source_approval import (
    approve_case_source_selection,
    request_case_source_selection,
)


def test_waiting_and_approval_create_no_attempt_model_or_process(
    tmp_path: Path,
) -> None:
    store = CaseStore.initialize(tmp_path / "case", "analysis-001")
    external = tmp_path / "external"
    external.mkdir()
    source = external / "moved.feb"
    source.write_bytes(b"current")
    resolution = resolve_source(SourceExpectation(
        role="baseline-feb",
        expected_sha256=sha256_file(source),
        expected_bytes=source.stat().st_size,
        preferred_path=external / "missing.feb",
        search_roots=(external,),
    ))
    request = request_case_source_selection(
        store,
        analysis_id="analysis-001",
        blocker_digest="A" * 64,
        resolution=resolution,
        requested_at="2026-07-30T00:00:00Z",
        nonce="nonce-source-001",
    )
    waiting = store.replay()
    assert waiting["harness"]["case_state"] == "WAITING_FOR_HUMAN"
    assert list((store.case_dir / "02_Model").iterdir()) == []
    assert {
        path.name
        for path in (store.case_dir / "90_Temporary").iterdir()
        if path.is_dir()
    } <= {"harness-lock-events"}
    record = approve_case_source_selection(
        store,
        request.request_path,
        selected_path=source,
        presented_nonce="nonce-source-001",
        approval_text="Use this verified moved source.",
        actor_kind="human",
        source_channel="codex-desktop",
        approved_at="2026-07-30T00:01:00Z",
    )
    approved = read_event_log(store.event_log)[-1]
    assert approved.event_type == "SOURCE_SELECTION_APPROVED"
    assert (
        approved.payload["approval_record_sha256"]
        == sha256_file(record.record_path)
    )
    assert (
        approved.payload["candidate_set_digest"]
        == record.candidate_set_digest
    )
    assert store.replay()["harness"]["case_state"] == CaseState.CASE_CREATED
    assert list((store.case_dir / "02_Model").iterdir()) == []
```

- [ ] **Step 2: Prove RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_source_approval.py `
  apps/febio_cae_harness/tests/integration/test_source_selection_gate.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.source_approval'`.

- [ ] **Step 3: Implement the independent source-selection authority**

Create
`apps/febio_cae_harness/src/febio_cae_harness/source_approval.py`:

```python
from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from .case_state import CaseState
from .case_store import CaseStore
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .jsonio import ArtifactCollisionError, atomic_create_unique_artifact
from .provenance import SourceResolution


class ApprovalBindingError(ValueError):
    """Source-selection approval fields do not match the bound request."""


@dataclass(frozen=True)
class SourceSelectionApprovalRequest:
    schema_version: int
    purpose: str
    request_id: str
    analysis_id: str
    blocker_digest: str
    candidate_set_digest: str
    candidates: tuple[dict[str, object], ...]
    requested_at: str
    nonce: str
    request_path: Path


@dataclass(frozen=True)
class SourceSelectionApprovalRecord:
    schema_version: int
    purpose: str
    request_id: str
    analysis_id: str
    blocker_digest: str
    candidate_set_digest: str
    selected_canonical_path: str
    selected_sha256: str
    selected_bytes: int
    approval_text: str
    actor_kind: str
    source_channel: str
    approved_at: str
    nonce: str
    record_path: Path


def _request_body(request: SourceSelectionApprovalRequest) -> dict[str, object]:
    value = asdict(request)
    value.pop("request_path")
    value["candidates"] = list(request.candidates)
    return value


def create_source_selection_request(
    case_dir: Path,
    *,
    analysis_id: str,
    blocker_digest: str,
    resolution: SourceResolution,
    requested_at: str,
    nonce: str | None = None,
) -> SourceSelectionApprovalRequest:
    if resolution.selected is not None:
        raise ApprovalBindingError("source is already resolved exactly")
    candidates = tuple(
        candidate.digest_value()
        for candidate in resolution.candidates_matching_expected
    )
    if not candidates:
        raise ApprovalBindingError("approval requires at least one hash match")
    nonce_value = nonce or secrets.token_urlsafe(32)
    request_seed = {
        "analysis_id": analysis_id,
        "blocker_digest": blocker_digest,
        "candidate_set_digest": resolution.candidate_set_digest,
        "candidates": list(candidates),
        "requested_at": requested_at,
        "nonce": nonce_value,
    }
    request_id = sha256_bytes(canonical_json_bytes(request_seed))
    path = (
        case_dir
        / "01_Input"
        / f"source-selection-request-{request_id}.json"
    )
    request = SourceSelectionApprovalRequest(
        schema_version=1,
        purpose="SOURCE_SELECTION",
        request_id=request_id,
        analysis_id=analysis_id,
        blocker_digest=blocker_digest,
        candidate_set_digest=resolution.candidate_set_digest,
        candidates=candidates,
        requested_at=requested_at,
        nonce=nonce_value,
        request_path=path,
    )
    atomic_create_unique_artifact(
        path,
        canonical_json_bytes(_request_body(request)) + b"\n",
    )
    return request


def _load_request(path: Path) -> SourceSelectionApprovalRequest:
    value = json.loads(path.read_text(encoding="utf-8"))
    return SourceSelectionApprovalRequest(
        schema_version=int(value["schema_version"]),
        purpose=str(value["purpose"]),
        request_id=str(value["request_id"]),
        analysis_id=str(value["analysis_id"]),
        blocker_digest=str(value["blocker_digest"]),
        candidate_set_digest=str(value["candidate_set_digest"]),
        candidates=tuple(value["candidates"]),
        requested_at=str(value["requested_at"]),
        nonce=str(value["nonce"]),
        request_path=path,
    )


def approve_source_selection(
    request_path: Path,
    *,
    selected_path: Path,
    presented_nonce: str,
    approval_text: str,
    actor_kind: str,
    source_channel: str,
    approved_at: str,
) -> SourceSelectionApprovalRecord:
    request = _load_request(request_path)
    record_path = request_path.with_name(
        f"source-selection-approval-{request.request_id}.json"
    )
    if record_path.exists():
        raise ApprovalBindingError("source-selection nonce was already used")
    if request.purpose != "SOURCE_SELECTION":
        raise ApprovalBindingError("request purpose is not source selection")
    if presented_nonce != request.nonce:
        raise ApprovalBindingError("source-selection nonce mismatch")
    if actor_kind != "human":
        raise ApprovalBindingError("actor must be human")
    if not approval_text.strip():
        raise ApprovalBindingError("approval text must not be blank")
    selected = selected_path.resolve(strict=True)
    selected_digest = sha256_file(selected)
    selected_bytes = selected.stat().st_size
    selected_value = {
        "canonical_path": str(selected),
        "bytes": selected_bytes,
        "sha256": selected_digest,
        "modified_ns": selected.stat().st_mtime_ns,
    }
    if selected_value not in request.candidates:
        raise ApprovalBindingError("selected path/hash/bytes is not a candidate")
    record = SourceSelectionApprovalRecord(
        schema_version=1,
        purpose="SOURCE_SELECTION",
        request_id=request.request_id,
        analysis_id=request.analysis_id,
        blocker_digest=request.blocker_digest,
        candidate_set_digest=request.candidate_set_digest,
        selected_canonical_path=str(selected),
        selected_sha256=selected_digest,
        selected_bytes=selected_bytes,
        approval_text=approval_text,
        actor_kind=actor_kind,
        source_channel=source_channel,
        approved_at=approved_at,
        nonce=presented_nonce,
        record_path=record_path,
    )
    body = asdict(record)
    body.pop("record_path")
    try:
        atomic_create_unique_artifact(
            record_path,
            canonical_json_bytes(body) + b"\n",
        )
    except ArtifactCollisionError as error:
        raise ApprovalBindingError(
            "source-selection nonce was already used"
        ) from error
    return record


def request_case_source_selection(
    store: CaseStore,
    *,
    analysis_id: str,
    blocker_digest: str,
    resolution: SourceResolution,
    requested_at: str,
    nonce: str | None = None,
) -> SourceSelectionApprovalRequest:
    request = create_source_selection_request(
        store.case_dir,
        analysis_id=analysis_id,
        blocker_digest=blocker_digest,
        resolution=resolution,
        requested_at=requested_at,
        nonce=nonce,
    )
    current = CaseState(store.replay()["harness"]["case_state"])
    store.append(
        "SOURCE_SELECTION_REQUIRED",
        CaseState.WAITING_FOR_HUMAN,
        {
            "blocker_digest": blocker_digest,
            "candidate_set_digest": resolution.candidate_set_digest,
            "request_id": request.request_id,
            "resume_state": current.value,
            "process_started": False,
        },
    )
    return request


def approve_case_source_selection(
    store: CaseStore,
    request_path: Path,
    **approval: object,
) -> SourceSelectionApprovalRecord:
    record = approve_source_selection(request_path, **approval)
    manifest = store.replay()
    resume = CaseState(manifest["harness"]["resume_state"])
    store.append(
        "SOURCE_SELECTION_APPROVED",
        resume,
        {
            "request_id": record.request_id,
            "record_path": str(record.record_path),
            "approval_record_sha256": sha256_file(record.record_path),
            "candidate_set_digest": record.candidate_set_digest,
            "selected_canonical_path": record.selected_canonical_path,
            "selected_sha256": record.selected_sha256,
            "selected_bytes": record.selected_bytes,
            "condition_cleared": True,
        },
    )
    return record
```

- [ ] **Step 4: Add request and record schemas**

Create both schemas with these complete contents:

`apps/febio_cae_harness/src/febio_cae_harness/schemas/source-selection-approval-request.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/source-selection-approval-request.schema.json",
  "type": "object",
  "required": [
    "schema_version", "purpose", "request_id", "analysis_id",
    "blocker_digest", "candidate_set_digest", "candidates",
    "requested_at", "nonce"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "purpose": {"const": "SOURCE_SELECTION"},
    "request_id": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "analysis_id": {"type": "string", "minLength": 1},
    "blocker_digest": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "candidate_set_digest": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "candidates": {"type": "array", "minItems": 1, "items": {"type": "object"}},
    "requested_at": {"type": "string", "format": "date-time"},
    "nonce": {"type": "string", "minLength": 16}
  },
  "additionalProperties": false
}
```

`apps/febio_cae_harness/src/febio_cae_harness/schemas/source-selection-approval-record.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/source-selection-approval-record.schema.json",
  "type": "object",
  "required": [
    "schema_version", "purpose", "request_id", "analysis_id",
    "blocker_digest", "candidate_set_digest", "selected_canonical_path",
    "selected_sha256", "selected_bytes", "approval_text", "actor_kind",
    "source_channel", "approved_at", "nonce"
  ],
  "properties": {
    "schema_version": {"const": 1},
    "purpose": {"const": "SOURCE_SELECTION"},
    "request_id": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "analysis_id": {"type": "string", "minLength": 1},
    "blocker_digest": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "candidate_set_digest": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "selected_canonical_path": {"type": "string", "minLength": 1},
    "selected_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"},
    "selected_bytes": {"type": "integer", "minimum": 0},
    "approval_text": {"type": "string", "minLength": 1},
    "actor_kind": {"const": "human"},
    "source_channel": {"type": "string", "minLength": 1},
    "approved_at": {"type": "string", "format": "date-time"},
    "nonce": {"type": "string", "minLength": 16}
  },
  "additionalProperties": false
}
```

Set the cumulative resource inventory to:

```python
EXPECTED_RESOURCES = {
    "schemas/case-event.schema.json",
    "schemas/case-manifest.schema.json",
    "schemas/command-result.schema.json",
    "schemas/input-record.schema.json",
    "schemas/preexisting-inventory.schema.json",
    "schemas/source-expectation.schema.json",
    "schemas/source-selection-approval-record.schema.json",
    "schemas/source-selection-approval-request.schema.json",
}
```

- [ ] **Step 5: Verify focused GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_source_approval.py `
  apps/febio_cae_harness/tests/integration/test_source_selection_gate.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: `PASS` with exit code `0`; waiting and approval launch no process,
create no attempt directory, and copy no FEB into `02_Model`.

- [ ] **Step 6: Commit the source-selection gate**

```powershell
git add `
  apps/febio_cae_harness/src/febio_cae_harness/source_approval.py `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/source-selection-approval-request.schema.json `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/source-selection-approval-record.schema.json `
  apps/febio_cae_harness/tests/unit/test_source_approval.py `
  apps/febio_cae_harness/tests/integration/test_source_selection_gate.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: gate unresolved CAE source selection"
```

Expected: one commit and a clean `git status --short`.

## Task 6: Enforce a Windows single-writer case lock

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/locks.py`
- Create: `apps/febio_cae_harness/tests/integration/test_locks.py`

**Interfaces:**

- Consumes: a persistent byte-lock path, immutable lock-event directory, `recover_stale_lock`, PID/create-time identity, and `psutil`.
- Produces: `CaseLock` as a context manager, create-new `lock-acquired-{token}.json` / `lock-released-{token}.json`, `LockRecovery` evidence for verified stale tokens, and deterministic `CASE_LOCKED` or `STALE_CASE_LOCK` failures; it never overwrites or deletes lock history.

- [ ] **Step 1: Add a real two-process Windows lock test**

Create `apps/febio_cae_harness/tests/integration/test_locks.py`:

```python
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from febio_cae_harness.locks import CaseLock, CaseLocked, StaleCaseLock


HOLDER = r"""
import sys
from pathlib import Path
from febio_cae_harness.locks import CaseLock
lock = CaseLock(Path(sys.argv[1]), Path(sys.argv[2]))
with lock:
    print("READY", flush=True)
    sys.stdin.readline()
"""

CRASHER = r"""
import os
import sys
from pathlib import Path
from febio_cae_harness.locks import CaseLock
lock = CaseLock(Path(sys.argv[1]), Path(sys.argv[2]))
lock.__enter__()
print("ACQUIRED", flush=True)
os._exit(17)
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows contract")
def test_second_process_receives_case_locked(tmp_path: Path) -> None:
    lock_path = tmp_path / "case.lock"
    event_dir = tmp_path / "harness-lock-events"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            HOLDER,
            str(lock_path),
            str(event_dir),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "READY"
        with pytest.raises(CaseLocked, match="CASE_LOCKED"):
            with CaseLock(lock_path, event_dir):
                raise AssertionError("second writer acquired the lock")
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert holder.wait(timeout=10) == 0


def test_history_is_create_new_and_prior_bytes_never_change(tmp_path: Path) -> None:
    lock_path = tmp_path / "case.lock"
    event_dir = tmp_path / "harness-lock-events"
    with CaseLock(lock_path, event_dir) as first:
        assert first.token is not None
    first_history = {
        path.name: path.read_bytes() for path in event_dir.iterdir()
    }
    assert len(first_history) == 2
    with CaseLock(lock_path, event_dir) as second:
        assert second.token is not None
    assert all(
        (event_dir / name).read_bytes() == data
        for name, data in first_history.items()
    )
    assert len(list(event_dir.glob("lock-acquired-*.json"))) == 2
    assert len(list(event_dir.glob("lock-released-*.json"))) == 2


def test_pid_reuse_is_stale_and_requires_explicit_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "case.lock"
    event_dir = tmp_path / "harness-lock-events"
    event_dir.mkdir()
    stale = event_dir / "lock-acquired-stale-token.json"
    stale.write_text(
        json.dumps({
            "schema_version": 1,
            "event": "acquired",
            "token": "stale-token",
            "pid": 424242,
            "process_create_time": 1.0,
            "acquired_at": "2026-07-30T00:00:00Z",
            "recovered_tokens": []
        }) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(
        "febio_cae_harness.locks.psutil.pid_exists",
        lambda pid: True,
    )

    class ReusedProcess:
        def create_time(self) -> float:
            return 2.0

    monkeypatch.setattr(
        "febio_cae_harness.locks.psutil.Process",
        lambda pid=None: ReusedProcess(),
    )
    with pytest.raises(StaleCaseLock, match="--recover-stale-lock"):
        with CaseLock(lock_path, event_dir):
            raise AssertionError("stale lock was silently recovered")
    with CaseLock(
        lock_path,
        event_dir,
        recover_stale_lock=True,
    ) as acquired:
        assert acquired.recovery is not None
        assert acquired.recovery.stale_tokens == ("stale-token",)
    assert stale.read_text(encoding="utf-8").endswith("\n")
    with CaseLock(lock_path, event_dir) as resumed:
        assert resumed.recovery is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows contract")
def test_crashed_owner_leaves_unmatched_acquisition_for_recovery(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "case.lock"
    event_dir = tmp_path / "harness-lock-events"
    crashed = subprocess.run(
        [
            sys.executable,
            "-c",
            CRASHER,
            str(lock_path),
            str(event_dir),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert crashed.returncode == 17
    assert crashed.stdout.strip() == "ACQUIRED"
    with pytest.raises(StaleCaseLock):
        with CaseLock(lock_path, event_dir):
            raise AssertionError("crash history was ignored")
    with CaseLock(
        lock_path,
        event_dir,
        recover_stale_lock=True,
    ) as recovered:
        assert recovered.recovery is not None
        assert len(recovered.recovery.stale_tokens) == 1
```

- [ ] **Step 2: Prove RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/integration/test_locks.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.locks'`.

- [ ] **Step 3: Implement the held-handle lock**

Create `apps/febio_cae_harness/src/febio_cae_harness/locks.py`:

```python
from __future__ import annotations

import json
import msvcrt
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import psutil

from .hashing import canonical_json_bytes
from .jsonio import atomic_create_artifact


class CaseLocked(RuntimeError):
    """Another live writer owns the case lock."""


class StaleCaseLock(RuntimeError):
    """Dead-owner metadata requires explicit recovery authorization."""


@dataclass(frozen=True)
class LockRecovery:
    stale_tokens: tuple[str, ...]
    prior_acquisitions: tuple[dict[str, object], ...]


class CaseLock:
    def __init__(
        self,
        lock_path: Path,
        event_dir: Path,
        *,
        recover_stale_lock: bool = False,
    ) -> None:
        self.lock_path = lock_path
        self.event_dir = event_dir
        self.recover_stale_lock = recover_stale_lock
        self._stream: BinaryIO | None = None
        self.recovery: LockRecovery | None = None
        self.token: str | None = None

    def _unmatched_acquisitions(self) -> tuple[dict[str, object], ...]:
        acquired: dict[str, dict[str, object]] = {}
        released: set[str] = set()
        recovered: set[str] = set()
        if not self.event_dir.exists():
            return ()
        for path in sorted(self.event_dir.glob("lock-*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise StaleCaseLock(
                    f"STALE_CASE_LOCK: invalid immutable history: {path.name}"
                ) from error
            if not isinstance(value, dict) or value.get("schema_version") != 1:
                raise StaleCaseLock(
                    f"STALE_CASE_LOCK: invalid event schema: {path.name}"
                )
            token = value.get("token")
            if not isinstance(token, str) or not token:
                raise StaleCaseLock(
                    f"STALE_CASE_LOCK: event token missing: {path.name}"
                )
            if value.get("event") == "acquired":
                if token in acquired:
                    raise StaleCaseLock(
                        f"STALE_CASE_LOCK: duplicate acquisition: {token}"
                    )
                acquired[token] = value
                recovered_tokens = value.get("recovered_tokens", [])
                if not isinstance(recovered_tokens, list) or not all(
                    isinstance(item, str) for item in recovered_tokens
                ):
                    raise StaleCaseLock(
                        f"STALE_CASE_LOCK: invalid recovery list: {path.name}"
                    )
                recovered.update(recovered_tokens)
            elif value.get("event") == "released":
                released.add(token)
            else:
                raise StaleCaseLock(
                    f"STALE_CASE_LOCK: unknown event: {path.name}"
                )
        orphan_releases = released - set(acquired)
        if orphan_releases:
            raise StaleCaseLock(
                "STALE_CASE_LOCK: release without acquisition: "
                + ",".join(sorted(orphan_releases))
            )
        unknown_recoveries = recovered - set(acquired)
        if unknown_recoveries:
            raise StaleCaseLock(
                "STALE_CASE_LOCK: recovery references unknown token: "
                + ",".join(sorted(unknown_recoveries))
            )
        return tuple(
            acquired[token]
            for token in sorted(set(acquired) - released - recovered)
        )

    @staticmethod
    def _same_live_process(acquisition: dict[str, object]) -> bool:
        pid = acquisition.get("pid")
        create_time = acquisition.get("process_create_time")
        if not isinstance(pid, int) or not isinstance(create_time, (int, float)):
            raise StaleCaseLock("STALE_CASE_LOCK: invalid process identity")
        if not psutil.pid_exists(pid):
            return False
        try:
            observed = psutil.Process(pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        return abs(observed - float(create_time)) < 0.001

    def _unlock_handle(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            stream.close()

    def __enter__(self) -> "CaseLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.lock_path.open("a+b", buffering=0)
        if self.lock_path.stat().st_size == 0:
            stream.write(b"\0")
            stream.flush()
            os.fsync(stream.fileno())
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            stream.close()
            raise CaseLocked("CASE_LOCKED: another writer owns the handle") from error
        self._stream = stream
        try:
            unmatched = self._unmatched_acquisitions()
            if any(self._same_live_process(item) for item in unmatched):
                raise CaseLocked(
                    "CASE_LOCKED: immutable history identifies a live owner"
                )
            if unmatched and not self.recover_stale_lock:
                raise StaleCaseLock(
                    "STALE_CASE_LOCK: retry with --recover-stale-lock"
                )
            if unmatched:
                self.recovery = LockRecovery(
                    stale_tokens=tuple(str(item["token"]) for item in unmatched),
                    prior_acquisitions=unmatched,
                )
            self.event_dir.mkdir(parents=True, exist_ok=True)
            token = uuid.uuid4().hex
            acquisition = {
                "schema_version": 1,
                "event": "acquired",
                "token": token,
                "pid": os.getpid(),
                "process_create_time": psutil.Process().create_time(),
                "acquired_at": datetime.now(UTC).isoformat().replace(
                    "+00:00",
                    "Z",
                ),
                "recovered_tokens": (
                    [] if self.recovery is None
                    else list(self.recovery.stale_tokens)
                ),
            }
            atomic_create_artifact(
                self.event_dir / f"lock-acquired-{token}.json",
                canonical_json_bytes(acquisition) + b"\n",
            )
            self.token = token
            return self
        except BaseException:
            self._unlock_handle()
            raise

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        token = self.token
        self.token = None
        if token is None:
            self._unlock_handle()
            return
        release = {
            "schema_version": 1,
            "event": "released",
            "token": token,
            "pid": os.getpid(),
            "released_at": datetime.now(UTC).isoformat().replace(
                "+00:00",
                "Z",
            ),
            "outcome": "error" if exc_type is not None else "success",
        }
        try:
            atomic_create_artifact(
                self.event_dir / f"lock-released-{token}.json",
                canonical_json_bytes(release) + b"\n",
            )
        finally:
            self._unlock_handle()
```

- [ ] **Step 4: Verify focused GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/integration/test_locks.py -q
```

Expected on Windows: `4 passed` and exit code `0`; every prior lock-event byte
is unchanged and no lock file is deleted.

- [ ] **Step 5: Commit lock enforcement**

```powershell
git add `
  apps/febio_cae_harness/src/febio_cae_harness/locks.py `
  apps/febio_cae_harness/tests/integration/test_locks.py
git commit -m "feat: enforce single writer case locks"
```

Expected: one commit and a clean `git status --short`.

## Task 7: Build the event-authoritative CaseStore and legacy adoption

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/case_store.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/case-manifest.schema.json`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/preexisting-inventory.schema.json`
- Create: `apps/febio_cae_harness/tests/integration/test_case_store.py`
- Create: `apps/febio_cae_harness/tests/fixtures/case/legacy-case-manifest.json`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**

- Consumes: workspace-validated case directory, `CaseEvent`, state transitions, `CaseLock`, immutable/manifest IO primitives, and an optional legacy `CASE_MANIFEST.json`.
- Produces: `CaseStore.initialize()`, manifest-and-full-inventory-bound `CaseStore.adopt_existing()`, `CaseStore.load_preexisting_inventory()`, `CaseStore.locked()`, `CaseTransaction.replay()`, `CaseTransaction.append()`, convenience `append()`, and authoritative `replay()`; it persists event-first, repairs only stale manifest projections, never reconstructs events from a manifest, and preserves legacy/inventory bytes create-new.

- [ ] **Step 1: Add crash, replay, layout, and adoption tests**

Create `apps/febio_cae_harness/tests/fixtures/case/legacy-case-manifest.json`:

```json
{
  "analysis_id": "legacy-001",
  "owner": "mechanical-team",
  "legacy_status": "prepared"
}
```

Create `apps/febio_cae_harness/tests/integration/test_case_store.py`:

```python
import json
from pathlib import Path

import pytest

from febio_cae_harness.case_state import CaseState
from febio_cae_harness.case_store import CaseStore
from febio_cae_harness.events import EventLogCorrupt, read_event_log
from febio_cae_harness.hashing import sha256_bytes, sha256_file


EXPECTED_TOP_LEVEL = {
    "README.md",
    "CASE_MANIFEST.json",
    "01_Input",
    "02_Model",
    "03_Result",
    "04_Report",
    "05_Verification",
    "90_Temporary",
}


def write_inventory(case: Path, destination: Path) -> Path:
    files = [
        {
            "relative_path": path.relative_to(case).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(item for item in case.rglob("*") if item.is_file())
    ]
    destination.write_text(
        json.dumps(
            {"schema_version": 1, "files": files},
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination


def test_initialize_creates_exact_layout_and_replayable_projection(
    tmp_path: Path,
) -> None:
    store = CaseStore.initialize(tmp_path / "case", "analysis-001")
    assert {item.name for item in store.case_dir.iterdir()} == EXPECTED_TOP_LEVEL
    manifest = store.replay()
    assert manifest["analysis_id"] == "analysis-001"
    assert manifest["harness"]["case_state"] == "CASE_CREATED"
    assert len(read_event_log(store.event_log)) == 1
    assert (
        manifest["harness"]["last_event_sha256"]
        == read_event_log(store.event_log)[-1].event_sha256
    )


def test_event_fsync_precedes_manifest_and_startup_repairs_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = CaseStore.initialize(tmp_path / "case", "analysis-001")
    original = store.manifest_path.read_bytes()

    def crash(path: Path, value: object) -> None:
        raise OSError("injected manifest replace failure")

    monkeypatch.setattr(
        "febio_cae_harness.case_store.atomic_replace_manifest",
        crash,
    )
    with pytest.raises(OSError, match="injected"):
        store.append(
            "INPUT_INSPECTED",
            CaseState.INPUT_INSPECTED,
            {"input_record_set_digest": "A" * 64},
        )
    assert store.manifest_path.read_bytes() == original
    assert len(read_event_log(store.event_log)) == 2
    monkeypatch.undo()
    repaired = CaseStore.open(store.case_dir).replay(repair_manifest=True)
    assert repaired["harness"]["case_state"] == "INPUT_INSPECTED"
    assert store.manifest_path.read_bytes() != original


def test_torn_event_log_fails_closed_without_truncation(tmp_path: Path) -> None:
    store = CaseStore.initialize(tmp_path / "case", "analysis-001")
    original = store.event_log.read_bytes() + b'{"event_id":"torn"'
    store.event_log.write_bytes(original)
    with pytest.raises(EventLogCorrupt, match="EVENT_LOG_CORRUPT"):
        store.replay(repair_manifest=True)
    assert store.event_log.read_bytes() == original


def test_projection_core_mismatch_is_repaired_from_events(tmp_path: Path) -> None:
    store = CaseStore.initialize(tmp_path / "case", "analysis-001")
    stale = json.loads(store.manifest_path.read_text(encoding="utf-8"))
    stale["harness"]["case_state"] = "SOLVED"
    store.manifest_path.write_text(json.dumps(stale), encoding="utf-8")
    replayed = CaseStore.open(store.case_dir).replay(repair_manifest=True)
    assert replayed["harness"]["case_state"] == "CASE_CREATED"
    assert json.loads(store.manifest_path.read_text(encoding="utf-8")) == replayed


def test_adoption_preserves_legacy_bytes_and_fields(tmp_path: Path) -> None:
    case = tmp_path / "legacy"
    case.mkdir()
    source = (
        Path(__file__).parents[1]
        / "fixtures"
        / "case"
        / "legacy-case-manifest.json"
    )
    legacy_bytes = source.read_bytes()
    (case / "CASE_MANIFEST.json").write_bytes(legacy_bytes)
    legacy_note = case / "preexisting-note.txt"
    legacy_note.write_text("retain\n", encoding="utf-8")
    inventory_value = {
        "schema_version": 1,
        "files": [
            {
                "relative_path": "CASE_MANIFEST.json",
                "bytes": len(legacy_bytes),
                "sha256": sha256_bytes(legacy_bytes),
            },
            {
                "relative_path": "preexisting-note.txt",
                "bytes": legacy_note.stat().st_size,
                "sha256": sha256_file(legacy_note),
            },
        ],
    }
    inventory_path = tmp_path / "preexisting-inventory.json"
    inventory_path.write_text(
        json.dumps(inventory_value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    store = CaseStore.adopt_existing(
        case,
        expected_manifest_sha256=sha256_bytes(legacy_bytes),
        preexisting_inventory_json=inventory_path,
        expected_preexisting_inventory_sha256=sha256_file(inventory_path),
    )
    digest = sha256_bytes(legacy_bytes)
    backup = (
        case
        / "05_Verification"
        / "harness"
        / "legacy-manifests"
        / f"legacy-manifest-{digest}.json"
    )
    assert backup.read_bytes() == legacy_bytes
    manifest = store.replay()
    assert manifest["owner"] == "mechanical-team"
    assert manifest["legacy_status"] == "prepared"
    assert manifest["harness"]["case_state"] == "CASE_CREATED"
    assert sha256_file(backup) == digest
    inventory_artifact = (
        case
        / "05_Verification"
        / "harness"
        / "preexisting-inventories"
        / f"preexisting-inventory-{sha256_file(inventory_path)}.json"
    )
    assert inventory_artifact.read_bytes() == inventory_path.read_bytes()
    adopted = read_event_log(store.event_log)[0]
    assert (
        adopted.payload["preexisting_inventory_artifact_sha256"]
        == sha256_file(inventory_artifact)
    )
    assert store.load_preexisting_inventory() == inventory_value


def test_adoption_rejects_manifest_drift(tmp_path: Path) -> None:
    case = tmp_path / "legacy-drift"
    case.mkdir()
    manifest = case / "CASE_MANIFEST.json"
    manifest.write_text('{"analysis_id":"changed"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="LEGACY_MANIFEST_HASH_MISMATCH"):
        CaseStore.adopt_existing(
            case,
            expected_manifest_sha256="A" * 64,
            preexisting_inventory_json=tmp_path / "not-read.json",
            expected_preexisting_inventory_sha256="B" * 64,
        )


def test_adoption_rejects_inventory_file_drift_before_case_write(
    tmp_path: Path,
) -> None:
    case = tmp_path / "legacy-inventory-drift"
    case.mkdir()
    manifest = case / "CASE_MANIFEST.json"
    manifest.write_text('{"analysis_id":"legacy"}\n', encoding="utf-8")
    original_case_names = {item.name for item in case.iterdir()}
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({
        "schema_version": 1,
        "files": [{
            "relative_path": "CASE_MANIFEST.json",
            "bytes": manifest.stat().st_size,
            "sha256": "A" * 64
        }]
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="PREEXISTING_FILE_HASH_MISMATCH"):
        CaseStore.adopt_existing(
            case,
            expected_manifest_sha256=sha256_file(manifest),
            preexisting_inventory_json=inventory,
            expected_preexisting_inventory_sha256=sha256_file(inventory),
        )
    assert {item.name for item in case.iterdir()} == original_case_names


def test_adoption_rejects_preexisting_harness_owned_paths(tmp_path: Path) -> None:
    case = tmp_path / "owned-collision"
    owned = case / "05_Verification" / "harness" / "events.jsonl"
    owned.parent.mkdir(parents=True)
    manifest = case / "CASE_MANIFEST.json"
    manifest.write_text('{"analysis_id":"legacy"}\n', encoding="utf-8")
    owned.write_text("{}\n", encoding="utf-8")
    inventory = write_inventory(case, tmp_path / "collision-inventory.json")
    with pytest.raises(ValueError, match="PREEXISTING_HARNESS_OWNERSHIP_COLLISION"):
        CaseStore.adopt_existing(
            case,
            expected_manifest_sha256=sha256_file(manifest),
            preexisting_inventory_json=inventory,
            expected_preexisting_inventory_sha256=sha256_file(inventory),
        )
```

Update the resource expectation:

```python
EXPECTED_RESOURCES = {
    "schemas/case-event.schema.json",
    "schemas/case-manifest.schema.json",
    "schemas/command-result.schema.json",
}
```

- [ ] **Step 2: Prove RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/integration/test_case_store.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.case_store'`.

- [ ] **Step 3: Implement projection hashing and replay**

Create `apps/febio_cae_harness/src/febio_cae_harness/case_store.py`:

```python
from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .case_state import (
    CaseState,
    TransitionContext,
    require_transition,
)
from .events import (
    CaseEvent,
    append_event,
    build_event,
    read_event_log,
)
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .jsonio import (
    atomic_create_unique_artifact,
    atomic_replace_manifest,
)
from .locks import CaseLock
from .schema import validate_schema

_DIRECTORIES = (
    "01_Input",
    "02_Model",
    "03_Result",
    "04_Report",
    "05_Verification",
    "90_Temporary",
)
_EVENT_LOG_RELATIVE = "05_Verification/harness/events.jsonl"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def _projection_core_sha256(manifest: dict[str, Any]) -> str:
    core = copy.deepcopy(manifest)
    harness = core["harness"]
    harness.pop("projection_core_sha256", None)
    harness.pop("last_event_sha256", None)
    return sha256_bytes(canonical_json_bytes(core))


def _project_event(
    current: dict[str, Any],
    *,
    event_id: str,
    event_type: str,
    to_state: CaseState,
    payload: dict[str, Any],
    event_sha256: str,
    projection_core_sha256: str,
) -> dict[str, Any]:
    projected = copy.deepcopy(current)
    if event_type in {"CASE_INITIALIZED", "LEGACY_ADOPTED"}:
        base = payload.get("manifest_base")
        if not isinstance(base, dict):
            raise ValueError("initial event requires manifest_base")
        projected = copy.deepcopy(base)
    harness = projected.setdefault("harness", {})
    harness.update({
        "schema_version": 1,
        "case_state": to_state.value,
        "event_log": _EVENT_LOG_RELATIVE,
        "last_event_id": event_id,
        "last_event_sha256": event_sha256,
        "projection_core_sha256": projection_core_sha256,
        "event_count": int(harness.get("event_count", 0)) + 1,
    })
    if to_state in {CaseState.WAITING_FOR_HUMAN, CaseState.CANCELLED}:
        resume_state = payload.get("resume_state")
        if not isinstance(resume_state, str):
            raise ValueError("waiting/cancelled event requires resume_state")
        harness["resume_state"] = resume_state
    elif event_type not in {
        "BLOCKER_RECORDED",
        "LOCK_RECOVERED",
        "PROJECTION_REPAIRED",
        "SOURCE_SELECTION_REQUESTED",
        "SOURCE_SELECTION_APPROVED",
        "INTENT_APPROVAL_REQUESTED",
    }:
        harness.pop("resume_state", None)
    return projected


def _replay_events(events: tuple[CaseEvent, ...]) -> dict[str, Any]:
    if not events or events[0].event_type not in {
        "CASE_INITIALIZED",
        "LEGACY_ADOPTED",
    }:
        raise ValueError("event log lacks an initialization event")
    projection: dict[str, Any] = {}
    previous_state: CaseState | None = None
    for event in events:
        if event.from_state != previous_state:
            raise ValueError("event state chain mismatch")
        projection = _project_event(
            projection,
            event_id=event.event_id,
            event_type=event.event_type,
            to_state=event.to_state,
            payload=event.payload,
            event_sha256=event.event_sha256,
            projection_core_sha256=event.projection_core_sha256,
        )
        if _projection_core_sha256(projection) != event.projection_core_sha256:
            raise ValueError("event projection_core_sha256 mismatch")
        previous_state = event.to_state
    return projection


class CaseTransaction:
    def __init__(
        self,
        store: "CaseStore",
        *,
        recover_stale_lock: bool,
    ) -> None:
        self.store = store
        self._lock = CaseLock(
            store.lock_path,
            store.lock_event_dir,
            recover_stale_lock=recover_stale_lock,
        )
        self._entered = False

    def __enter__(self) -> "CaseTransaction":
        acquired = self._lock.__enter__()
        self._entered = True
        try:
            return self._finish_enter(acquired)
        except BaseException:
            self._entered = False
            self._lock.__exit__(None, None, None)
            raise

    def _finish_enter(self, acquired: CaseLock) -> "CaseTransaction":
        self.store.replay(repair_manifest=True)
        if acquired.recovery is not None:
            state = CaseState(
                self.store.replay()["harness"]["case_state"]
            )
            self.store._append_unlocked(
                "LOCK_RECOVERED",
                state,
                {
                    "stale_tokens": list(acquired.recovery.stale_tokens),
                    "prior_acquisitions": list(
                        acquired.recovery.prior_acquisitions
                    ),
                },
            )
        return self

    def append(
        self,
        event_type: str,
        to_state: CaseState,
        payload: dict[str, Any],
    ) -> CaseEvent:
        if not self._entered:
            raise RuntimeError("CaseTransaction is not entered")
        return self.store._append_unlocked(event_type, to_state, payload)

    def replay(self) -> dict[str, Any]:
        if not self._entered:
            raise RuntimeError("CaseTransaction is not entered")
        return self.store.replay(repair_manifest=False)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self._lock.__exit__(exc_type, exc, traceback)
        finally:
            self._entered = False


class CaseStore:
    def __init__(self, case_dir: Path) -> None:
        self.case_dir = case_dir.resolve(strict=True)
        self.manifest_path = self.case_dir / "CASE_MANIFEST.json"
        self.event_log = self.case_dir / _EVENT_LOG_RELATIVE
        self.lock_path = self.case_dir / "90_Temporary" / "harness.case.lock"
        self.lock_event_dir = (
            self.case_dir / "90_Temporary" / "harness-lock-events"
        )

    @classmethod
    def open(cls, case_dir: Path) -> "CaseStore":
        return cls(case_dir)

    @classmethod
    def initialize(cls, case_dir: Path, analysis_id: str) -> "CaseStore":
        if not analysis_id.strip():
            raise ValueError("analysis_id must not be blank")
        case_dir.mkdir(parents=False, exist_ok=False)
        for name in _DIRECTORIES:
            (case_dir / name).mkdir()
        (case_dir / "README.md").write_text(
            "# FEBio CAE case\n\n"
            "Generated artifacts are immutable; active work stays in "
            "`90_Temporary`.\n",
            encoding="utf-8",
            newline="\n",
        )
        store = cls(case_dir)
        store._append_initial(
            "CASE_INITIALIZED",
            {"analysis_id": analysis_id},
        )
        return store

    @classmethod
    def adopt_existing(
        cls,
        case_dir: Path,
        *,
        expected_manifest_sha256: str,
        preexisting_inventory_json: Path,
        expected_preexisting_inventory_sha256: str,
    ) -> "CaseStore":
        case = case_dir.resolve(strict=True)
        manifest_path = case / "CASE_MANIFEST.json"
        legacy_bytes = manifest_path.read_bytes()
        actual_legacy_sha256 = sha256_bytes(legacy_bytes)
        if actual_legacy_sha256 != expected_manifest_sha256:
            raise ValueError(
                "LEGACY_MANIFEST_HASH_MISMATCH: "
                f"expected {expected_manifest_sha256}, "
                f"observed {actual_legacy_sha256}"
            )
        inventory_path = preexisting_inventory_json.resolve(strict=True)
        inventory_bytes = inventory_path.read_bytes()
        inventory_sha256 = sha256_bytes(inventory_bytes)
        if inventory_sha256 != expected_preexisting_inventory_sha256:
            raise ValueError(
                "PREEXISTING_INVENTORY_HASH_MISMATCH: "
                f"expected {expected_preexisting_inventory_sha256}, "
                f"observed {inventory_sha256}"
            )
        inventory = json.loads(inventory_bytes.decode("utf-8"))
        validate_schema("preexisting-inventory", inventory)
        listed_paths: set[str] = set()
        for record in inventory["files"]:
            relative = Path(record["relative_path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("PREEXISTING_INVENTORY_PATH_UNSAFE")
            relative_key = relative.as_posix()
            if (
                relative_key.startswith("05_Verification/harness/")
                or relative_key.startswith("90_Temporary/attempts/")
            ):
                raise ValueError(
                    "PREEXISTING_HARNESS_OWNERSHIP_COLLISION:"
                    f"{relative_key}"
                )
            listed_paths.add(relative_key)
            current = (case / relative).resolve(strict=True)
            if current.stat().st_size != record["bytes"]:
                raise ValueError(
                    f"PREEXISTING_FILE_SIZE_MISMATCH:{relative_key}"
                )
            if sha256_file(current) != record["sha256"]:
                raise ValueError(
                    f"PREEXISTING_FILE_HASH_MISMATCH:{relative_key}"
                )
        actual_paths = {
            path.relative_to(case).as_posix()
            for path in case.rglob("*")
            if path.is_file()
        }
        if actual_paths != listed_paths:
            raise ValueError(
                "PREEXISTING_INVENTORY_SET_MISMATCH: "
                f"listed={sorted(listed_paths)}, actual={sorted(actual_paths)}"
            )
        try:
            legacy = json.loads(legacy_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid legacy manifest: {error}") from error
        if not isinstance(legacy, dict) or "harness" in legacy:
            raise ValueError("manifest is not an unadopted legacy object")
        for name in _DIRECTORIES:
            (case / name).mkdir(exist_ok=True)
        digest = sha256_bytes(legacy_bytes)
        backup = (
            case
            / "05_Verification"
            / "harness"
            / "legacy-manifests"
            / f"legacy-manifest-{digest}.json"
        )
        atomic_create_unique_artifact(backup, legacy_bytes)
        inventory_artifact = (
            case
            / "05_Verification"
            / "harness"
            / "preexisting-inventories"
            / f"preexisting-inventory-{inventory_sha256}.json"
        )
        atomic_create_unique_artifact(inventory_artifact, inventory_bytes)
        store = cls(case)
        store._append_initial(
            "LEGACY_ADOPTED",
            legacy,
            extra_payload={
                "preexisting_inventory_artifact_path": (
                    inventory_artifact.relative_to(case).as_posix()
                ),
                "preexisting_inventory_artifact_sha256": sha256_file(
                    inventory_artifact
                ),
            },
        )
        return store

    def _append_initial(
        self,
        event_type: str,
        manifest_base: dict[str, Any],
        *,
        extra_payload: dict[str, Any] | None = None,
    ) -> CaseEvent:
        event_id = str(uuid.uuid4())
        payload = {
            "manifest_base": copy.deepcopy(manifest_base),
            **({} if extra_payload is None else extra_payload),
        }
        target = _project_event(
            {},
            event_id=event_id,
            event_type=event_type,
            to_state=CaseState.CASE_CREATED,
            payload=payload,
            event_sha256="",
            projection_core_sha256="",
        )
        core_hash = _projection_core_sha256(target)
        event = build_event(
            event_id=event_id,
            event_type=event_type,
            occurred_at=_utc_now(),
            from_state=None,
            to_state=CaseState.CASE_CREATED,
            payload=payload,
            prev_event_sha256=None,
            previous_manifest_sha256=None,
            projection_core_sha256=core_hash,
        )
        append_event(self.event_log, event)
        projected = _replay_events((event,))
        atomic_replace_manifest(self.manifest_path, projected)
        return event

    def replay(self, *, repair_manifest: bool = False) -> dict[str, Any]:
        events = read_event_log(self.event_log)
        projected = _replay_events(events)
        encoded = canonical_json_bytes(projected) + b"\n"
        existing = (
            self.manifest_path.read_bytes()
            if self.manifest_path.exists()
            else None
        )
        if existing != encoded:
            if not repair_manifest:
                raise ValueError("MANIFEST_PROJECTION_STALE")
            atomic_replace_manifest(self.manifest_path, projected)
            if self.manifest_path.read_bytes() != encoded:
                raise OSError("manifest projection verification failed")
        return projected

    def load_preexisting_inventory(self) -> dict[str, Any]:
        events = read_event_log(self.event_log)
        first = events[0]
        relative = first.payload.get("preexisting_inventory_artifact_path")
        expected = first.payload.get("preexisting_inventory_artifact_sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise ValueError("case has no bound pre-existing inventory")
        expected_parent = (
            self.case_dir
            / "05_Verification"
            / "harness"
            / "preexisting-inventories"
        ).resolve(strict=True)
        path = (self.case_dir / relative).resolve(strict=True)
        if path.parent != expected_parent:
            raise ValueError("bound pre-existing inventory path is invalid")
        data = path.read_bytes()
        if sha256_bytes(data) != expected:
            raise ValueError("PREEXISTING_INVENTORY_ARTIFACT_DRIFT")
        value = json.loads(data.decode("utf-8"))
        validate_schema("preexisting-inventory", value)
        return value

    def _append_unlocked(
        self,
        event_type: str,
        to_state: CaseState,
        payload: dict[str, Any],
    ) -> CaseEvent:
        events = read_event_log(self.event_log)
        current = _replay_events(events)
        source = CaseState(current["harness"]["case_state"])
        if source != to_state:
            context = TransitionContext(
                resume_state=(
                    None
                    if current["harness"].get("resume_state") is None
                    else CaseState(current["harness"]["resume_state"])
                ),
                condition_cleared=bool(payload.get("condition_cleared", False)),
            )
            require_transition(source, to_state, context)
        event_id = str(uuid.uuid4())
        target = _project_event(
            current,
            event_id=event_id,
            event_type=event_type,
            to_state=to_state,
            payload=payload,
            event_sha256="",
            projection_core_sha256="",
        )
        core_hash = _projection_core_sha256(target)
        previous_manifest = (
            sha256_bytes(self.manifest_path.read_bytes())
            if self.manifest_path.exists()
            else None
        )
        event = build_event(
            event_id=event_id,
            event_type=event_type,
            occurred_at=_utc_now(),
            from_state=source,
            to_state=to_state,
            payload=payload,
            prev_event_sha256=events[-1].event_sha256,
            previous_manifest_sha256=previous_manifest,
            projection_core_sha256=core_hash,
        )
        append_event(self.event_log, event)
        projected = _replay_events(read_event_log(self.event_log))
        atomic_replace_manifest(self.manifest_path, projected)
        manifest_bytes = self.manifest_path.read_bytes()
        persisted = json.loads(manifest_bytes.decode("utf-8"))
        if _projection_core_sha256(persisted) != core_hash:
            raise OSError("manifest projection core verification failed")
        if sha256_bytes(manifest_bytes) != sha256_bytes(
            canonical_json_bytes(projected) + b"\n"
        ):
            raise OSError("manifest full-file hash verification failed")
        return event

    def append(
        self,
        event_type: str,
        to_state: CaseState,
        payload: dict[str, Any],
        *,
        recover_stale_lock: bool = False,
    ) -> CaseEvent:
        with self.locked(
            recover_stale_lock=recover_stale_lock,
        ) as transaction:
            return transaction.append(event_type, to_state, payload)

    def locked(
        self,
        *,
        recover_stale_lock: bool = False,
    ) -> CaseTransaction:
        return CaseTransaction(
            self,
            recover_stale_lock=recover_stale_lock,
        )
```

- [ ] **Step 4: Add and validate the manifest schema**

Create
`apps/febio_cae_harness/src/febio_cae_harness/schemas/case-manifest.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/case-manifest.schema.json",
  "type": "object",
  "required": ["harness"],
  "properties": {
    "analysis_id": {"type": "string", "minLength": 1},
    "harness": {
      "type": "object",
      "required": [
        "schema_version",
        "case_state",
        "event_log",
        "last_event_id",
        "last_event_sha256",
        "projection_core_sha256",
        "event_count"
      ],
      "properties": {
        "schema_version": {"const": 1},
        "case_state": {"type": "string", "minLength": 1},
        "event_log": {
          "const": "05_Verification/harness/events.jsonl"
        },
        "last_event_id": {"type": "string", "minLength": 1},
        "last_event_sha256": {
          "type": "string",
          "pattern": "^[0-9A-F]{64}$"
        },
        "projection_core_sha256": {
          "type": "string",
          "pattern": "^[0-9A-F]{64}$"
        },
        "event_count": {"type": "integer", "minimum": 1},
        "resume_state": {"type": "string", "minLength": 1}
      },
      "additionalProperties": false
    }
  },
  "additionalProperties": true
}
```

- [ ] **Step 5: Add the full pre-existing inventory schema**

Create
`apps/febio_cae_harness/src/febio_cae_harness/schemas/preexisting-inventory.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/preexisting-inventory.schema.json",
  "type": "object",
  "required": ["schema_version", "files"],
  "properties": {
    "schema_version": {"const": 1},
    "files": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "required": ["relative_path", "bytes", "sha256"],
        "properties": {
          "relative_path": {
            "type": "string",
            "minLength": 1,
            "pattern": "^(?![A-Za-z]:)(?!/)(?!.*(?:^|/)\\.\\.(?:/|$)).+$"
          },
          "bytes": {"type": "integer", "minimum": 0},
          "sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
        },
        "additionalProperties": false
      }
    }
  },
  "additionalProperties": false
}
```

Add `"schemas/preexisting-inventory.schema.json"` to
`EXPECTED_RESOURCES`.

- [ ] **Step 6: Verify focused GREEN and all earlier contracts**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit `
  apps/febio_cae_harness/tests/contract `
  apps/febio_cae_harness/tests/integration/test_locks.py `
  apps/febio_cae_harness/tests/integration/test_case_store.py -q
```

Expected: `PASS` with exit code `0`; the crash test leaves the event prefix
intact and repairs only `CASE_MANIFEST.json`.

- [ ] **Step 7: Commit the case store**

```powershell
git add `
  apps/febio_cae_harness/src/febio_cae_harness/case_store.py `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/case-manifest.schema.json `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/preexisting-inventory.schema.json `
  apps/febio_cae_harness/tests/integration/test_case_store.py `
  apps/febio_cae_harness/tests/fixtures/case/legacy-case-manifest.json `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: add replayable event authoritative case store"
```

Expected: one commit and a clean `git status --short`.

## Task 4: Encode the complete case-state transition contract

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/case_state.py`
- Create: `apps/febio_cae_harness/tests/unit/test_case_state.py`

**Interfaces:**

- Consumes: current `CaseState`, requested `CaseState`, and `TransitionContext`.
- Produces: the full `CaseState` enum, immutable `ALLOWED_TRANSITIONS`, and `require_transition()`; `WAITING_FOR_HUMAN` and `CANCELLED` can resume only their recorded state after an explicit cleared condition.

- [ ] **Step 1: Add exhaustive transition tests**

Create `apps/febio_cae_harness/tests/unit/test_case_state.py`:

```python
import pytest

from febio_cae_harness.case_state import (
    ALLOWED_TRANSITIONS,
    CaseState,
    TransitionContext,
    TransitionError,
    require_transition,
)


def test_table_classifies_every_distinct_state_pair() -> None:
    for source in CaseState:
        for target in CaseState:
            if source == target:
                continue
            expected = target in ALLOWED_TRANSITIONS.get(source, frozenset())
            context = TransitionContext()
            if source in {CaseState.WAITING_FOR_HUMAN, CaseState.CANCELLED}:
                context = TransitionContext(
                    resume_state=target,
                    condition_cleared=True,
                )
            if expected or source in {
                CaseState.WAITING_FOR_HUMAN,
                CaseState.CANCELLED,
            }:
                if source in {
                    CaseState.WAITING_FOR_HUMAN,
                    CaseState.CANCELLED,
                }:
                    require_transition(source, target, context)
                elif expected:
                    require_transition(source, target, context)
                else:
                    with pytest.raises(TransitionError):
                        require_transition(source, target, context)
            else:
                with pytest.raises(TransitionError):
                    require_transition(source, target, context)


@pytest.mark.parametrize(
    "source",
    [CaseState.WAITING_FOR_HUMAN, CaseState.CANCELLED],
)
def test_resume_requires_recorded_state_and_cleared_condition(
    source: CaseState,
) -> None:
    with pytest.raises(TransitionError, match="resume_state"):
        require_transition(source, CaseState.INPUT_INSPECTED, TransitionContext())
    with pytest.raises(TransitionError, match="not cleared"):
        require_transition(
            source,
            CaseState.INPUT_INSPECTED,
            TransitionContext(
                resume_state=CaseState.INPUT_INSPECTED,
                condition_cleared=False,
            ),
        )
    with pytest.raises(TransitionError, match="resume_state"):
        require_transition(
            source,
            CaseState.INTENT_DRAFTED,
            TransitionContext(
                resume_state=CaseState.INPUT_INSPECTED,
                condition_cleared=True,
            ),
        )
    require_transition(
        source,
        CaseState.INPUT_INSPECTED,
        TransitionContext(
            resume_state=CaseState.INPUT_INSPECTED,
            condition_cleared=True,
        ),
    )


def test_same_state_is_not_a_transition() -> None:
    with pytest.raises(TransitionError, match="same-state"):
        require_transition(
            CaseState.CASE_CREATED,
            CaseState.CASE_CREATED,
            TransitionContext(),
        )
```

- [ ] **Step 2: Prove RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_case_state.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.case_state'`.

- [ ] **Step 3: Implement the enum and transition table**

Create `apps/febio_cae_harness/src/febio_cae_harness/case_state.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class TransitionError(ValueError):
    """The requested case-state transition is not permitted."""


class CaseState(StrEnum):
    CASE_CREATED = "CASE_CREATED"
    INPUT_INSPECTED = "INPUT_INSPECTED"
    INTENT_DRAFTED = "INTENT_DRAFTED"
    INTENT_APPROVED = "INTENT_APPROVED"
    MODEL_BUILT = "MODEL_BUILT"
    PREFLIGHT_PASSED = "PREFLIGHT_PASSED"
    SOLVED = "SOLVED"
    RESULT_VERIFIED = "RESULT_VERIFIED"
    REPORTED = "REPORTED"
    HUMAN_ACCEPTED = "HUMAN_ACCEPTED"
    MODEL_FAILED = "MODEL_FAILED"
    MESH_REJECTED = "MESH_REJECTED"
    SOLVE_FAILED = "SOLVE_FAILED"
    RESULT_INCOMPLETE = "RESULT_INCOMPLETE"
    INTENT_IMPACT_REVIEW = "INTENT_IMPACT_REVIEW"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    CANCELLED = "CANCELLED"


_TRANSITIONS = {
    CaseState.CASE_CREATED: frozenset({
        CaseState.INPUT_INSPECTED,
        CaseState.WAITING_FOR_HUMAN,
        CaseState.CANCELLED,
    }),
    CaseState.INPUT_INSPECTED: frozenset({
        CaseState.INTENT_DRAFTED,
        CaseState.WAITING_FOR_HUMAN,
        CaseState.CANCELLED,
    }),
    CaseState.INTENT_DRAFTED: frozenset({
        CaseState.INTENT_APPROVED,
        CaseState.WAITING_FOR_HUMAN,
        CaseState.CANCELLED,
    }),
    CaseState.INTENT_APPROVED: frozenset({
        CaseState.INTENT_DRAFTED,
        CaseState.MODEL_BUILT,
        CaseState.WAITING_FOR_HUMAN,
        CaseState.CANCELLED,
    }),
    CaseState.MODEL_BUILT: frozenset({
        CaseState.PREFLIGHT_PASSED,
        CaseState.MODEL_FAILED,
        CaseState.MESH_REJECTED,
        CaseState.WAITING_FOR_HUMAN,
        CaseState.CANCELLED,
    }),
    CaseState.PREFLIGHT_PASSED: frozenset({
        CaseState.SOLVED,
        CaseState.SOLVE_FAILED,
        CaseState.RESULT_INCOMPLETE,
        CaseState.CANCELLED,
    }),
    CaseState.SOLVED: frozenset({
        CaseState.RESULT_VERIFIED,
        CaseState.RESULT_INCOMPLETE,
        CaseState.INTENT_IMPACT_REVIEW,
    }),
    CaseState.RESULT_VERIFIED: frozenset({CaseState.REPORTED}),
    CaseState.REPORTED: frozenset({CaseState.HUMAN_ACCEPTED}),
    CaseState.HUMAN_ACCEPTED: frozenset(),
    CaseState.MODEL_FAILED: frozenset({
        CaseState.INPUT_INSPECTED,
        CaseState.WAITING_FOR_HUMAN,
        CaseState.CANCELLED,
    }),
    CaseState.MESH_REJECTED: frozenset({
        CaseState.INTENT_IMPACT_REVIEW,
        CaseState.WAITING_FOR_HUMAN,
        CaseState.CANCELLED,
    }),
    CaseState.SOLVE_FAILED: frozenset({
        CaseState.PREFLIGHT_PASSED,
        CaseState.INTENT_IMPACT_REVIEW,
        CaseState.WAITING_FOR_HUMAN,
        CaseState.CANCELLED,
    }),
    CaseState.RESULT_INCOMPLETE: frozenset({
        CaseState.PREFLIGHT_PASSED,
        CaseState.INTENT_IMPACT_REVIEW,
        CaseState.WAITING_FOR_HUMAN,
        CaseState.CANCELLED,
    }),
    CaseState.INTENT_IMPACT_REVIEW: frozenset({
        CaseState.INTENT_DRAFTED,
        CaseState.PREFLIGHT_PASSED,
        CaseState.WAITING_FOR_HUMAN,
        CaseState.CANCELLED,
    }),
    CaseState.WAITING_FOR_HUMAN: frozenset(),
    CaseState.CANCELLED: frozenset(),
}
ALLOWED_TRANSITIONS = MappingProxyType(_TRANSITIONS)


@dataclass(frozen=True)
class TransitionContext:
    resume_state: CaseState | None = None
    condition_cleared: bool = False


def require_transition(
    source: CaseState,
    target: CaseState,
    context: TransitionContext,
) -> None:
    if source == target:
        raise TransitionError("same-state evidence requires an allowed event type")
    if source in {CaseState.WAITING_FOR_HUMAN, CaseState.CANCELLED}:
        if context.resume_state is None or target != context.resume_state:
            raise TransitionError("target does not match recorded resume_state")
        if not context.condition_cleared:
            raise TransitionError("blocking or cancellation condition is not cleared")
        return
    if target not in ALLOWED_TRANSITIONS[source]:
        raise TransitionError(f"prohibited transition: {source} -> {target}")
```

- [ ] **Step 4: Verify focused GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_case_state.py -q
```

Expected: `PASS` with exit code `0`.

- [ ] **Step 5: Commit state policy**

```powershell
git add `
  apps/febio_cae_harness/src/febio_cae_harness/case_state.py `
  apps/febio_cae_harness/tests/unit/test_case_state.py
git commit -m "feat: encode case state transitions"
```

Expected: one commit and a clean `git status --short`.

## Task 5: Add canonical hash-chained case events

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/events.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/schemas/case-event.schema.json`
- Create: `apps/febio_cae_harness/tests/unit/test_events.py`
- Modify: `apps/febio_cae_harness/tests/contract/test_installed_resources.py`

**Interfaces:**

- Consumes: `CaseState`, canonical JSON/SHA helpers, a prior event hash, prior manifest hash, and target projection-core hash.
- Produces: immutable `CaseEvent`, `build_event()`, `append_event()`, and `read_event_log()`; all persisted event lines are canonical UTF-8 with exactly one LF.

- [ ] **Step 1: Add event-chain and corruption tests**

Create `apps/febio_cae_harness/tests/unit/test_events.py`:

```python
import json
from pathlib import Path

import pytest

from febio_cae_harness.case_state import CaseState
from febio_cae_harness.events import (
    EventLogCorrupt,
    append_event,
    build_event,
    read_event_log,
)
from febio_cae_harness.hashing import sha256_bytes


def event(
    previous: str | None = None,
    event_id: str = "event-0001",
):
    return build_event(
        event_id=event_id,
        event_type="CASE_INITIALIZED",
        occurred_at="2026-07-30T00:00:00.000000Z",
        from_state=None if previous is None else CaseState.CASE_CREATED,
        to_state=CaseState.CASE_CREATED,
        payload={"analysis_id": "analysis-001"},
        prev_event_sha256=previous,
        previous_manifest_sha256=None,
        projection_core_sha256="A" * 64,
    )


def test_event_hash_is_canonical_and_excludes_its_own_hash() -> None:
    first = event()
    body = first.to_dict()
    claimed = body.pop("event_sha256")
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert claimed == sha256_bytes(encoded)


def test_append_preserves_prefix_and_replays_chain(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    first = event()
    append_event(log, first)
    prefix = log.read_bytes()
    second = build_event(
        event_id="event-0002",
        event_type="INPUT_INSPECTED",
        occurred_at="2026-07-30T00:01:00.000000Z",
        from_state=CaseState.CASE_CREATED,
        to_state=CaseState.INPUT_INSPECTED,
        payload={"input_record_set_digest": "B" * 64},
        prev_event_sha256=first.event_sha256,
        previous_manifest_sha256="C" * 64,
        projection_core_sha256="D" * 64,
    )
    append_event(log, second)
    assert log.read_bytes().startswith(prefix)
    assert read_event_log(log) == (first, second)


@pytest.mark.parametrize("mutation", ["edited", "truncated", "reordered", "torn"])
def test_corrupt_log_fails_closed(tmp_path: Path, mutation: str) -> None:
    log = tmp_path / "events.jsonl"
    first = event()
    second = build_event(
        event_id="event-0002",
        event_type="BLOCKER_RECORDED",
        occurred_at="2026-07-30T00:01:00.000000Z",
        from_state=CaseState.CASE_CREATED,
        to_state=CaseState.CASE_CREATED,
        payload={"blocker_digest": "E" * 64},
        prev_event_sha256=first.event_sha256,
        previous_manifest_sha256="F" * 64,
        projection_core_sha256="A" * 64,
    )
    lines = [
        (json.dumps(first.to_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode(),
        (json.dumps(second.to_dict(), sort_keys=True, separators=(",", ":")) + "\n").encode(),
    ]
    if mutation == "edited":
        lines[0] = lines[0].replace(b"analysis-001", b"analysis-999")
    elif mutation == "truncated":
        lines = lines[:1]
        lines[0] = lines[0][:-1]
    elif mutation == "reordered":
        lines.reverse()
    else:
        lines[1] = lines[1][:-17]
    log.write_bytes(b"".join(lines))
    with pytest.raises(EventLogCorrupt, match="EVENT_LOG_CORRUPT"):
        read_event_log(log)


def test_duplicate_blocker_digest_is_rejected(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    first = event()
    blocker = build_event(
        event_id="event-0002",
        event_type="BLOCKER_RECORDED",
        occurred_at="2026-07-30T00:01:00.000000Z",
        from_state=CaseState.CASE_CREATED,
        to_state=CaseState.CASE_CREATED,
        payload={"blocker_digest": "E" * 64},
        prev_event_sha256=first.event_sha256,
        previous_manifest_sha256="F" * 64,
        projection_core_sha256="A" * 64,
    )
    append_event(log, first)
    append_event(log, blocker)
    duplicate = build_event(
        event_id="event-0003",
        event_type="BLOCKER_RECORDED",
        occurred_at="2026-07-30T00:02:00.000000Z",
        from_state=CaseState.CASE_CREATED,
        to_state=CaseState.CASE_CREATED,
        payload={"blocker_digest": "E" * 64},
        prev_event_sha256=blocker.event_sha256,
        previous_manifest_sha256="B" * 64,
        projection_core_sha256="A" * 64,
    )
    with pytest.raises(ValueError, match="duplicate blocker_digest"):
        append_event(log, duplicate)
```

Update the expected resources in
`apps/febio_cae_harness/tests/contract/test_installed_resources.py`:

```python
EXPECTED_RESOURCES = {
    "schemas/case-event.schema.json",
    "schemas/command-result.schema.json",
}
```

- [ ] **Step 2: Prove RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_events.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: collection fails with
`ModuleNotFoundError: No module named 'febio_cae_harness.events'`.

- [ ] **Step 3: Implement event construction and append-only persistence**

Create `apps/febio_cae_harness/src/febio_cae_harness/events.py`:

```python
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .case_state import CaseState
from .hashing import canonical_json_bytes, sha256_bytes

_SHA256_FIELDS = (
    "prev_event_sha256",
    "event_sha256",
    "previous_manifest_sha256",
    "projection_core_sha256",
)
SAME_STATE_EVENT_TYPES = frozenset({
    "BLOCKER_RECORDED",
    "LOCK_RECOVERED",
    "PROJECTION_REPAIRED",
    "SOURCE_SELECTION_REQUESTED",
    "SOURCE_SELECTION_APPROVED",
    "INTENT_APPROVAL_REQUESTED",
})


class EventLogCorrupt(ValueError):
    """The append-only case-event chain is invalid."""


@dataclass(frozen=True)
class CaseEvent:
    event_id: str
    event_type: str
    occurred_at: str
    from_state: CaseState | None
    to_state: CaseState
    payload: dict[str, Any]
    prev_event_sha256: str | None
    previous_manifest_sha256: str | None
    projection_core_sha256: str
    event_sha256: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["from_state"] = (
            None if self.from_state is None else self.from_state.value
        )
        value["to_state"] = self.to_state.value
        return value


def build_event(
    *,
    event_id: str,
    event_type: str,
    occurred_at: str,
    from_state: CaseState | None,
    to_state: CaseState,
    payload: dict[str, Any],
    prev_event_sha256: str | None,
    previous_manifest_sha256: str | None,
    projection_core_sha256: str,
) -> CaseEvent:
    if from_state == to_state and event_type not in SAME_STATE_EVENT_TYPES:
        raise ValueError("same-state event type is not allowed")
    body = {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "from_state": None if from_state is None else from_state.value,
        "to_state": to_state.value,
        "payload": payload,
        "prev_event_sha256": prev_event_sha256,
        "previous_manifest_sha256": previous_manifest_sha256,
        "projection_core_sha256": projection_core_sha256,
    }
    return CaseEvent(
        **{
            **body,
            "from_state": from_state,
            "to_state": to_state,
            "event_sha256": sha256_bytes(canonical_json_bytes(body)),
        }
    )


def _event_from_dict(value: dict[str, Any]) -> CaseEvent:
    supplied_hash = value.get("event_sha256")
    body = {key: item for key, item in value.items() if key != "event_sha256"}
    if supplied_hash != sha256_bytes(canonical_json_bytes(body)):
        raise EventLogCorrupt("EVENT_LOG_CORRUPT: event hash mismatch")
    try:
        event = CaseEvent(
            event_id=str(body["event_id"]),
            event_type=str(body["event_type"]),
            occurred_at=str(body["occurred_at"]),
            from_state=(
                None
                if body["from_state"] is None
                else CaseState(str(body["from_state"]))
            ),
            to_state=CaseState(str(body["to_state"])),
            payload=dict(body["payload"]),
            prev_event_sha256=body["prev_event_sha256"],
            previous_manifest_sha256=body["previous_manifest_sha256"],
            projection_core_sha256=str(body["projection_core_sha256"]),
            event_sha256=str(supplied_hash),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise EventLogCorrupt(f"EVENT_LOG_CORRUPT: invalid event: {error}") from error
    for field in _SHA256_FIELDS:
        item = getattr(event, field)
        if item is not None and (
            len(item) != 64 or item.upper() != item
            or any(character not in "0123456789ABCDEF" for character in item)
        ):
            raise EventLogCorrupt(
                f"EVENT_LOG_CORRUPT: invalid SHA-256 in {field}"
            )
    return event


def read_event_log(path: Path) -> tuple[CaseEvent, ...]:
    if not path.exists():
        return ()
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise EventLogCorrupt("EVENT_LOG_CORRUPT: torn final line")
    events: list[CaseEvent] = []
    previous: str | None = None
    event_ids: set[str] = set()
    blocker_digests: set[str] = set()
    for line_number, line in enumerate(raw.splitlines(), start=1):
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EventLogCorrupt(
                f"EVENT_LOG_CORRUPT: line {line_number}: {error}"
            ) from error
        if not isinstance(value, dict):
            raise EventLogCorrupt("EVENT_LOG_CORRUPT: event must be an object")
        event = _event_from_dict(value)
        if event.prev_event_sha256 != previous:
            raise EventLogCorrupt("EVENT_LOG_CORRUPT: chain order mismatch")
        if event.event_id in event_ids:
            raise EventLogCorrupt("EVENT_LOG_CORRUPT: duplicate event_id")
        event_ids.add(event.event_id)
        if event.event_type == "BLOCKER_RECORDED":
            digest = event.payload.get("blocker_digest")
            if not isinstance(digest, str):
                raise EventLogCorrupt(
                    "EVENT_LOG_CORRUPT: blocker_digest missing"
                )
            if digest in blocker_digests:
                raise EventLogCorrupt(
                    "EVENT_LOG_CORRUPT: duplicate blocker_digest"
                )
            blocker_digests.add(digest)
        previous = event.event_sha256
        events.append(event)
    return tuple(events)


def append_event(path: Path, event: CaseEvent) -> None:
    existing = read_event_log(path)
    expected_previous = None if not existing else existing[-1].event_sha256
    if event.prev_event_sha256 != expected_previous:
        raise EventLogCorrupt("EVENT_LOG_CORRUPT: append chain mismatch")
    if event.event_id in {item.event_id for item in existing}:
        raise ValueError("duplicate event_id")
    if event.event_type == "BLOCKER_RECORDED":
        digest = event.payload.get("blocker_digest")
        if any(
            item.event_type == "BLOCKER_RECORDED"
            and item.payload.get("blocker_digest") == digest
            for item in existing
        ):
            raise ValueError("duplicate blocker_digest")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical_json_bytes(event.to_dict()) + b"\n"
    descriptor = os.open(
        path,
        os.O_BINARY | os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        0o600,
    )
    try:
        written = os.write(descriptor, line)
        if written != len(line):
            raise OSError("short event-log write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
```

Create
`apps/febio_cae_harness/src/febio_cae_harness/schemas/case-event.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://febio-tools.local/case-event.schema.json",
  "type": "object",
  "required": [
    "event_id",
    "event_type",
    "occurred_at",
    "from_state",
    "to_state",
    "payload",
    "prev_event_sha256",
    "previous_manifest_sha256",
    "projection_core_sha256",
    "event_sha256"
  ],
  "properties": {
    "event_id": {"type": "string", "minLength": 1},
    "event_type": {"type": "string", "minLength": 1},
    "occurred_at": {"type": "string", "format": "date-time"},
    "from_state": {"type": ["string", "null"]},
    "to_state": {"type": "string", "minLength": 1},
    "payload": {"type": "object"},
    "prev_event_sha256": {
      "anyOf": [
        {"type": "null"},
        {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      ]
    },
    "previous_manifest_sha256": {
      "anyOf": [
        {"type": "null"},
        {"type": "string", "pattern": "^[0-9A-F]{64}$"}
      ]
    },
    "projection_core_sha256": {
      "type": "string",
      "pattern": "^[0-9A-F]{64}$"
    },
    "event_sha256": {"type": "string", "pattern": "^[0-9A-F]{64}$"}
  },
  "additionalProperties": false
}
```

- [ ] **Step 4: Verify focused GREEN**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_events.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py -q
```

Expected: `PASS` with exit code `0`.

- [ ] **Step 5: Commit event primitives**

```powershell
git add `
  apps/febio_cae_harness/src/febio_cae_harness/events.py `
  apps/febio_cae_harness/src/febio_cae_harness/schemas/case-event.schema.json `
  apps/febio_cae_harness/tests/unit/test_events.py `
  apps/febio_cae_harness/tests/contract/test_installed_resources.py
git commit -m "feat: add hash chained case events"
```

Expected: one commit and a clean `git status --short`.

---

### Task 2: Add canonical hashing and create-new/replace IO primitives

**Files:**

- Create: `apps/febio_cae_harness/src/febio_cae_harness/hashing.py`
- Create: `apps/febio_cae_harness/src/febio_cae_harness/jsonio.py`
- Create: `apps/febio_cae_harness/tests/unit/test_hashing.py`
- Create: `apps/febio_cae_harness/tests/unit/test_jsonio.py`

**Interfaces:**

- Consumes: `ExitCode` only through later callers; Python filesystem APIs.
- Produces: `ArtifactRef`, `ArtifactCollisionError`, `canonical_json_bytes`, `sha256_bytes`, `sha256_file`, `file_evidence`, `atomic_replace_manifest`, `atomic_create_artifact`, and `atomic_create_unique_artifact`.

- [ ] **Step 1: Write canonical hashing tests**

Create `tests/unit/test_hashing.py`:

```python
from hashlib import sha256

from febio_cae_harness.hashing import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)


def test_sha256_is_uppercase_and_streamed(tmp_path) -> None:
    path = tmp_path / "large.bin"
    data = b"abc" * 400_000
    path.write_bytes(data)
    expected = sha256(data).hexdigest().upper()
    assert sha256_bytes(data) == expected
    assert sha256_file(path, chunk_bytes=1024) == expected


def test_canonical_json_is_utf8_sorted_and_compact() -> None:
    value = {"z": "日本語", "a": [2, 1]}
    assert canonical_json_bytes(value) == (
        '{"a":[2,1],"z":"日本語"}'.encode("utf-8")
    )
```

- [ ] **Step 2: Write immutable-write and manifest-replacement tests**

Create `tests/unit/test_jsonio.py`:

```python
import json

import pytest

import febio_cae_harness.jsonio as jsonio
from febio_cae_harness.hashing import sha256_bytes
from febio_cae_harness.jsonio import (
    ArtifactCollisionError,
    atomic_create_artifact,
    atomic_replace_manifest,
)


def test_create_new_reuses_only_identical_bytes(tmp_path) -> None:
    target = tmp_path / "evidence.json"
    first = atomic_create_artifact(target, b"same")
    second = atomic_create_artifact(target, b"same")
    assert first.path == target.resolve()
    assert second.reused is True
    assert target.read_bytes() == b"same"
    with pytest.raises(ArtifactCollisionError):
        atomic_create_artifact(target, b"different")
    assert target.read_bytes() == b"same"


def test_racing_destination_is_never_replaced(tmp_path, monkeypatch) -> None:
    target = tmp_path / "result.bin"

    def race(source, destination) -> None:
        destination.write_bytes(b"racer")
        raise FileExistsError(destination)

    monkeypatch.setattr(jsonio, "_move_new", race)
    with pytest.raises(ArtifactCollisionError):
        atomic_create_artifact(target, b"ours")
    assert target.read_bytes() == b"racer"
    assert list(tmp_path.glob("*.partial")) == []


def test_manifest_replace_preserves_old_bytes_on_failure(
    tmp_path,
    monkeypatch,
) -> None:
    manifest = tmp_path / "CASE_MANIFEST.json"
    manifest.write_bytes(b'{"old":true}\n')

    def fail_replace(source, destination) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(jsonio.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        atomic_replace_manifest(manifest, {"new": True})
    assert manifest.read_bytes() == b'{"old":true}\n'


def test_created_artifact_evidence_matches_bytes(tmp_path) -> None:
    target = tmp_path / "record.json"
    payload = json.dumps({"value": 7}, separators=(",", ":")).encode("utf-8")
    artifact = atomic_create_artifact(target, payload)
    assert artifact.bytes == len(payload)
    assert artifact.sha256 == sha256_bytes(payload)
```

- [ ] **Step 3: Run the focused tests to verify RED**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_hashing.py `
  apps/febio_cae_harness/tests/unit/test_jsonio.py -q
```

Expected: collection fails with:

```text
ModuleNotFoundError: No module named 'febio_cae_harness.hashing'
```

- [ ] **Step 4: Implement canonical hashing and file evidence**

Create `src/febio_cae_harness/hashing.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest().upper()


def sha256_file(path: Path, chunk_bytes: int = 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest().upper()


def file_evidence(path: Path) -> dict[str, object]:
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "bytes": stat.st_size,
        "sha256": sha256_file(resolved),
        "mtime_utc": datetime.fromtimestamp(
            stat.st_mtime,
            tz=timezone.utc,
        ).isoformat(),
    }
```

- [ ] **Step 5: Implement immutable create-new writes**

Create `src/febio_cae_harness/jsonio.py`:

```python
from __future__ import annotations

import ctypes
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file


MOVEFILE_WRITE_THROUGH = 0x00000008
ERROR_FILE_EXISTS = 80
ERROR_ALREADY_EXISTS = 183


class ArtifactCollisionError(RuntimeError):
    """A create-new destination exists with different bytes."""


@dataclass(frozen=True)
class ArtifactRef:
    path: Path
    bytes: int
    sha256: str
    reused: bool = False


def _move_new(source: Path, destination: Path) -> None:
    if os.name != "nt":
        os.link(source, destination)
        source.unlink()
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file = kernel32.MoveFileExW
    move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move_file.restype = ctypes.c_int
    if move_file(str(source), str(destination), MOVEFILE_WRITE_THROUGH):
        return
    error = ctypes.get_last_error()
    if error in {ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS}:
        raise FileExistsError(destination)
    raise ctypes.WinError(error)


def _existing_ref(path: Path, expected: bytes) -> ArtifactRef | None:
    if not path.is_file():
        return None
    expected_hash = sha256_bytes(expected)
    if path.stat().st_size != len(expected) or sha256_file(path) != expected_hash:
        raise ArtifactCollisionError(f"different destination exists: {path}")
    return ArtifactRef(path.resolve(), len(expected), expected_hash, reused=True)


def atomic_create_artifact(path: Path, data: bytes) -> ArtifactRef:
    path = path.resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _existing_ref(path, data)
    if existing is not None:
        return existing
    partial = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if sha256_file(partial) != sha256_bytes(data):
            raise OSError("partial artifact hash mismatch")
        try:
            _move_new(partial, path)
        except FileExistsError as error:
            existing = _existing_ref(path, data)
            if existing is not None:
                return existing
            raise ArtifactCollisionError(
                f"racing destination differs: {path}"
            ) from error
        return ArtifactRef(path, len(data), sha256_bytes(data), reused=False)
    finally:
        if partial.exists():
            partial.unlink()


def atomic_create_unique_artifact(
    path: Path,
    data: bytes,
) -> ArtifactRef:
    preferred = path.resolve(strict=False)
    directory = preferred.parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        return atomic_create_artifact(preferred, data)
    except ArtifactCollisionError:
        source = Path(preferred.name)
        suffix = source.suffix
        stem = source.name[: -len(suffix)] if suffix else source.name
        digest = sha256_bytes(data)[:12]
        for counter in range(1, 1000):
            candidate = directory / f"{stem}-{digest}-{counter:03d}{suffix}"
            try:
                return atomic_create_artifact(candidate, data)
            except ArtifactCollisionError:
                continue
        raise ArtifactCollisionError("no unique artifact name available")


def atomic_replace_manifest(path: Path, value: object) -> None:
    path = path.resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(value) + b"\n"
    partial = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(partial, path)
    finally:
        if partial.exists():
            partial.unlink()
```

- [ ] **Step 6: Verify focused GREEN and package regression**

Run:

```powershell
& .\.venv\Scripts\python.exe -m pytest `
  apps/febio_cae_harness/tests/unit/test_hashing.py `
  apps/febio_cae_harness/tests/unit/test_jsonio.py `
  apps/febio_cae_harness/tests/unit/test_response.py `
  apps/febio_cae_harness/tests/contract/test_command_result_schema.py -q
```

Expected: `PASS` with exit code `0`.

- [ ] **Step 7: Commit IO primitives**

```powershell
git status --short
git add `
  apps/febio_cae_harness/src/febio_cae_harness/hashing.py `
  apps/febio_cae_harness/src/febio_cae_harness/jsonio.py `
  apps/febio_cae_harness/tests/unit/test_hashing.py `
  apps/febio_cae_harness/tests/unit/test_jsonio.py
git commit -m "feat: add canonical hashing and immutable IO"
```

Expected: one commit; `git status --short` is empty afterward.
