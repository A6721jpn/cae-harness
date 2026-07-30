# FEBio Workspace Organization Design

## 1. Purpose

Reorganize `C:\Users\backo\OneDrive\Documents\FEBio` so that:

- reusable FEBio tool development is physically isolated from CAE work;
- Git and GitHub are used only for tool development;
- every CAE file has an identifiable analysis case and lifecycle state;
- permanent models and results are clearly separated from deletable
  intermediates;
- all future LLM work follows the same placement, retention, validation, and
  deletion rules; and
- the first migration preserves every existing file until the user approves a
  separate deletion batch.

This design applies only to the current FEBio folder. Files under external
locations such as `C:\dev\FEBio` are referenced in case records when relevant
but are not moved by this migration.

## 2. Confirmed Constraints

- This workspace supports both FEBio tool development and CAE analysis.
- Tool development must use Git and GitHub.
- CAE work must be outside every Git working tree and must never be uploaded to
  GitHub.
- Both tool development and CAE work are performed by an LLM.
- Permanent CAE content includes:
  - the source input when no separate authoritative source exists;
  - `.fsm`;
  - `.feb`;
  - `.xplt`;
  - the final report;
  - the final solver evidence;
  - the final validation data; and
  - a SHA-256 manifest.
- Reproducible intermediates are temporary. Typical examples are `.msh`,
  `.inp`, `.vtu`, `.vol`, trial logs, repair candidates, caches, and rendered
  review images.
- No existing file is deleted during the initial migration.

## 3. Current-State Findings

The 2026-07-30 read-only inventory found:

- the FEBio folder itself is a Git repository;
- the root `.gitignore` is empty;
- tool source, CAE data, reports, caches, and linked worktrees are mixed below
  the same root;
- the working tree contains tracked modifications and many untracked files;
- multiple linked worktrees exist, including both clean and dirty worktrees;
- the repository HEAD and worktree set changed during the inventory, which
  proves that migration must begin with a fresh live-state audit;
- the folder contains approximately 2.75 GB;
- `.worktrees` accounts for approximately 1.26 GB;
- `.git` accounts for approximately 359 MB;
- `tmp` accounts for approximately 281 MB; and
- the initial migration must not move files while another task is actively
  writing them.

The two copies of
`02_Bottom Frame_v1.0,0728_C_Tet10.fsm` are byte-identical:

```text
D8CBB40887BF7C65A6987C9C047E70B9B9D20C51877A72C1CC2BDC1DD9461D59
```

The file `~02_Bottom Frame_v1.0,0728_C_Tet10_auto.fsm` has a different size
and SHA-256 and therefore is not an automatically deletable duplicate.

## 4. Target Top-Level Structure

```text
FEBio/
├─ AGENTS.md
├─ README.md
├─ 01_Tools/
│  ├─ febio-tools/
│  │  ├─ .git/
│  │  ├─ AGENTS.md
│  │  ├─ apps/
│  │  │  └─ febio_gmsh_launcher/
│  │  ├─ scripts/
│  │  │  ├─ post/
│  │  │  └─ tet10/
│  │  ├─ tests/
│  │  ├─ docs/
│  │  └─ policies/
│  ├─ _worktrees/
│  └─ _migration_backup/
└─ 02_CAE/
   ├─ AGENTS.md
   ├─ NO_GIT.md
   ├─ 00_Shared/
   │  ├─ Materials/
   │  └─ Templates/
   ├─ 01_Active/
   ├─ 02_Archive/
   ├─ 90_Needs_Review/
   ├─ 98_Delete_Review/
   └─ 99_Temporary/
```

The root is not a Git working tree. The only production Git repository below
the root is `01_Tools/febio-tools`.

Version-controlled policy templates live under
`01_Tools/febio-tools/policies`. Deployed copies at the workspace root and
under `02_CAE` remain effective even though those locations are outside Git.

## 5. CAE Case Structure

Every active or archived analysis uses:

```text
<Project>/
└─ <Analysis_ID>/
   ├─ README.md
   ├─ CASE_MANIFEST.json
   ├─ 01_Input/
   ├─ 02_Model/
   ├─ 03_Result/
   ├─ 04_Report/
   ├─ 05_Verification/
   └─ 90_Temporary/
```

New analysis IDs use:

```text
YYYY-MM-DD_<Project>_<Case>_<Purpose>_rNN
```

Directory names are ASCII and contain no commas. Existing filenames are
preserved during migration unless a confirmed application reference requires
an explicit path update. The original path is always recorded in the case
manifest.

### 5.1 Permanent locations

- `01_Input`: authoritative source input or a pointer to its authoritative
  location.
- `02_Model`: final `.fsm` and `.feb`.
- `03_Result`: final `.xplt`.
- `04_Report`: final human-readable HTML or PDF report.
- `05_Verification`: final solver log, solver status, validation JSON,
  `SHA256SUMS.txt`, and any case-specific evidence needed to interpret the
  result.

### 5.2 Temporary location

`90_Temporary` contains reproducible or superseded files such as:

- `.msh`;
- `.inp`;
- `.vtu`;
- `.vol`;
- BREP conversion artifacts;
- repair and meshing candidates;
- trial solver logs;
- temporary scripts;
- extracted runtimes;
- rendered review images; and
- caches.

No temporary result may be written directly into `02_Model` or `03_Result`.

## 6. Existing-File Mapping

### 6.1 Tool-development content

| Existing path or group | Destination |
|---|---|
| Current Git refs, branches, tags, and history | New `01_Tools/febio-tools` repository |
| `febio_gmsh_launcher` | `01_Tools/febio-tools/apps/febio_gmsh_launcher` |
| `step_tet10_pipeline.py` | `01_Tools/febio-tools/scripts/tet10` |
| `tet10_quality_artifacts.py` | `01_Tools/febio-tools/scripts/tet10` |
| `febio_vm_fos_post.py` | `01_Tools/febio-tools/scripts/post` |
| Reusable root tests | `01_Tools/febio-tools/tests` |
| Tool-specific plans and specifications | `01_Tools/febio-tools/docs` |
| Fusion STEP repair progress report | Corresponding tool report folder |
| Dirty tool worktrees | Recreated under `01_Tools/_worktrees` with dirty state preserved |

Historical CAE commits remain in Git history for auditability, but no new CAE
file is committed after migration.

### 6.2 0724_A repair trial

The three root-level 0724_A files move to:

```text
02_CAE/90_Needs_Review/Bottom_Frame/
└─ 2026-07-24_0724A_repair-trial/
   ├─ 01_Input/
   │  └─ 02_Bottom Frame_v1.0,0724_A_repaired_candidate.step
   ├─ 04_Report/
   │  └─ 02_Bottom Frame_v1.0,0724_A_repair_report.md
   └─ 90_Temporary/
      └─ 02_Bottom Frame_v1.0,0724_A_gmsh_test_only.msh
```

It remains in `90_Needs_Review` because no corresponding FSM, FEB, or XPLT
exists in the current folder.

### 6.3 0728_C Tet10 preparation

`02_Bottom_Frame_FEBio_CAD` and `02_Bottom_Frame_Tet10` merge into:

```text
02_CAE/01_Active/Bottom_Frame/
└─ 2026-07-28_0728C_tet10-preparation/
```

Allocation rules are:

- adopted repaired STEP: `01_Input`;
- one canonical FSM: `02_Model`;
- README and final HTML report: `04_Report`;
- final quality JSON, hashes, validation report, and legacy verification
  scripts: `05_Verification`;
- candidate STEP, BREP, VOL, MSH, INP, VTU, CSV, and trial logs:
  `90_Temporary`; and
- the different `~...auto.fsm`: a neighboring case folder under
  `90_Needs_Review`, not `90_Temporary`.

The identical delivery FSM is retained initially under
`98_Delete_Review/<migration-date>/Duplicates`. It is deleted only in a later
approved deletion batch.

This analysis stays active because the current folder does not contain its
final FEB and XPLT.

### 6.4 0729_C and later Bottom Frame work

Existing reports and CAE-specific LLM plans are grouped under:

```text
02_CAE/01_Active/Bottom_Frame/
├─ 2026-07-29_0729C_local-refinement/
├─ 2026-07-29_0729C_fatigue-recovery/
├─ 2026-07-29_0729C_vm-fos/
├─ 2026-07-29_0729C_m2-contact/
└─ 2026-07-30_0729C_local040-screw/
```

Each report moves to `04_Report`. Its data JSON, LLM design, LLM plan, source
hash record, and validation evidence move to `05_Verification`. When the
corresponding model or result remains outside the current folder, the case
README records the external path and the status `active-missing-local-model`.

Clean historical CAE worktrees are extracted into their case folders and then
become worktree-removal candidates. Their branches remain in Git history.
Dirty worktrees are never removed until their modifications and untracked files
have been preserved and independently verified.

### 6.5 Shared and temporary content

| Existing path | Destination |
|---|---|
| `material_library` | `02_CAE/00_Shared/Materials` |
| `tmp` | `02_CAE/99_Temporary/Legacy_tmp` |
| `.pytest_cache` | `02_CAE/98_Delete_Review/<migration-date>/Caches` |
| root `__pycache__` | `02_CAE/98_Delete_Review/<migration-date>/Caches` |
| empty `test-output` | `02_CAE/98_Delete_Review/<migration-date>/Empty` |
| unpaired report | `02_CAE/90_Needs_Review` |

## 7. LLM Routing Rules

At the start of every task, the LLM classifies the work:

```text
Reusable software, test, installer, or tool documentation
  -> 01_Tools/febio-tools
  -> Git and GitHub allowed

Real-model preprocessing, meshing, solving, post-processing, or reporting
  -> 02_CAE/01_Active
  -> Git and GitHub prohibited
```

A case-specific script remains under `90_Temporary/Scripts`. Promoting it to a
reusable tool is a separate tool-development task and requires:

1. replacement of real CAE data with a small synthetic fixture;
2. implementation under `01_Tools/febio-tools`;
3. tests;
4. Git review; and
5. a recorded tool commit in any later CAE manifest that uses it.

The LLM must stop before CAE work when:

- `git rev-parse --is-inside-work-tree` succeeds from the case directory;
- any `.git` directory or Git pointer file is found below `02_CAE`;
- the requested output path resolves under `01_Tools`; or
- an input path is ambiguous or points to an unverified revision.

## 8. CAE Completion Gate

A case may move from `01_Active` to `02_Archive` only when:

1. the final FSM, FEB, and XPLT exist;
2. a non-applicable format has an explicit technical justification in
   `README.md`;
3. the source input or authoritative external source is recorded;
4. the final solver log proves full progression and normal termination;
5. the final report exists;
6. `CASE_MANIFEST.json` records paths, byte sizes, and SHA-256 values;
7. `05_Verification/solver-status.json` records the executable, FEBio version,
   final time, converged-step count, warning summary, and termination status;
8. model, result, and report reopen checks pass;
9. mesh-quality and result checks required by that case are recorded; and
10. `90_Temporary` is empty or every remaining file has a documented retention
    reason.

An XPLT file that contains only initialization output is not a completed
result. A displayed mesh is not evidence of a valid solve.

## 9. Case Manifest

`CASE_MANIFEST.json` uses schema version 1 and contains:

```json
{
  "schema_version": 1,
  "analysis_id": "2026-07-30_Project_Case_Purpose_r01",
  "project": "Project",
  "status": "active",
  "git_prohibited": true,
  "created_at": "2026-07-30T00:00:00+09:00",
  "completed_at": null,
  "source_files": [],
  "model_files": [],
  "result_files": [],
  "report_files": [],
  "verification_files": [],
  "tool_versions": [],
  "solver": {
    "executable": null,
    "version": null,
    "status": "not-run",
    "final_time": null,
    "converged_steps": null
  },
  "temporary_status": "present"
}
```

Each file entry contains its current relative path, original path, role,
byte size, and SHA-256.

## 10. Deletion Workflow

The first migration performs no deletion.

For a later cleanup, the LLM creates `DELETE_CANDIDATES.csv` with:

```text
relative_path,size_bytes,sha256,classification,reason,
protected_by_manifest,proposed_at,status
```

The workflow is:

1. confirm that every candidate is temporary or a verified duplicate;
2. confirm that no candidate is the only source input, FSM, FEB, XPLT, report,
   final solver log, or validation record;
3. move candidates to `02_CAE/98_Delete_Review/<date>`;
4. rerun manifest, reopen, and solver-evidence checks;
5. present total size and exact paths to the user; and
6. permanently delete only after explicit user approval.

Deletion commands never target the workspace root, `01_Tools`, `02_CAE`,
`01_Active`, or `02_Archive` recursively.

## 11. Git Migration

The current root repository is not relocated in place. The safer sequence is:

1. capture a fresh inventory, Git ref list, worktree list, status, dirty patch,
   untracked-file manifest, and branch HEAD map;
2. confirm that no concurrent task is writing the affected paths;
3. create a no-hardlink local clone in a sibling migration staging directory;
4. verify every branch, tag, HEAD, and commit object required by the source;
5. restructure tool content in the staged clone;
6. recreate tool-development worktrees under the new common Git directory;
7. restore each dirty tool worktree's tracked changes and untracked files;
8. extract CAE-only worktree content into `02_CAE` without creating Git
   metadata there;
9. run tool tests in the staged clone;
10. move the verified staged clone to `01_Tools/febio-tools`;
11. move the old root Git administrative data and old worktree administrative
    data to `01_Tools/_migration_backup` under names that are not `.git`;
12. verify that the workspace root and `02_CAE` are outside a Git working tree;
    and
13. retain the migration backup until the user approves its later removal.

The two currently modified CAE-specific design/plan files are preserved as
working files in the corresponding CAE case. They are not accidentally folded
into a tool-only commit.

## 12. Migration Verification

The migration is accepted only when all checks pass:

- pre- and post-migration inventories reconcile;
- all permanent and review-held files match their pre-migration SHA-256;
- no existing file is absent unless it appears in the move ledger;
- all source branches, tags, and branch heads are present in the new
  repository;
- every dirty diff and untracked file from a preserved tool worktree matches
  the pre-migration snapshot;
- targeted tool tests pass;
- the canonical 0728_C FSM reopens or matches its verified source hash;
- `git rev-parse --is-inside-work-tree` fails from the workspace root and
  `02_CAE`;
- no `.git` directory or Git pointer file exists below `02_CAE`;
- CAE manifests resolve every moved permanent file;
- the delete-review ledger contains every duplicate and cache proposed for
  later deletion; and
- the original Git administrative data remains available in
  `_migration_backup`.

Any failed check stops the migration. The verified staging clone and move
ledger provide the recovery route; no cleanup proceeds until the mismatch is
resolved.

## 13. Deliverables

Implementation produces:

- the target folder structure;
- deployed root, tool, and CAE `AGENTS.md` rules;
- a human-readable workspace `README.md`;
- a verified tool repository and recreated tool worktrees;
- case-level README and JSON manifests for migrated CAE content;
- a pre/post move ledger;
- `DELETE_CANDIDATES.csv`;
- a migration verification report; and
- an intact migration backup pending separate user-approved deletion.
