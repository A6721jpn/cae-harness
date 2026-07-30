# FEBio Workspace Organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Physically separate Git-based FEBio tool development from non-Git CAE work, migrate every existing workspace file into an explicit lifecycle location, and install enforceable LLM rules without deleting existing data.

**Architecture:** Build and verify a staged replacement tool repository before deactivating the mixed root repository. Migrate CAE content into case folders outside every Git working tree, preserve uncertain and temporary files in review locations, then activate the new repository and verify Git refs, dirty worktrees, file hashes, case manifests, and the no-Git boundary.

**Tech Stack:** Windows PowerShell 5.1, Git, Python 3, pytest, JSON, SHA-256, FEBio/FEBio Studio file formats

## Global Constraints

- Workspace: `C:\Users\backo\OneDrive\Documents\FEBio`.
- The workspace root must not remain a Git working tree.
- `C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\febio-tools` is the only active Git repository created by this migration.
- `C:\Users\backo\OneDrive\Documents\FEBio\02_CAE` must be outside every Git working tree and contain no `.git` directory or Git pointer file.
- Tool development uses Git; real-model CAE data is never committed or uploaded.
- No existing file is deleted during this implementation.
- Existing dirty changes, untracked files, branches, tags, and linked-worktree content must be preserved.
- Permanent CAE content includes source input, FSM, FEB, XPLT, final report, final solver evidence, final validation, and SHA-256 manifest.
- Temporary CAE content moves only to case `90_Temporary`, shared `99_Temporary`, or `98_Delete_Review`.
- New directory names are ASCII and contain no commas; existing filenames remain unchanged during migration.
- External locations such as `C:\dev\FEBio` are recorded but not moved.
- All recursive move targets must be resolved and verified beneath the FEBio workspace or the explicitly named sibling staging directory before execution.
- The initial implementation stops on a hash, Git-ref, dirty-diff, or manifest mismatch.

---

## Final File Responsibilities

### Tool repository

- `01_Tools/febio-tools/apps/febio_gmsh_launcher/`: existing reusable launcher application.
- `01_Tools/febio-tools/scripts/tet10/step_tet10_pipeline.py`: reusable STEP-to-Tet10 pipeline.
- `01_Tools/febio-tools/scripts/tet10/tet10_quality_artifacts.py`: reusable Tet10 quality exports.
- `01_Tools/febio-tools/scripts/post/febio_vm_fos_post.py`: reusable post-processing utility.
- `01_Tools/febio-tools/scripts/workspace/workspace_guard.py`: validates the Git boundary and CAE case manifests.
- `01_Tools/febio-tools/tests/workspace/test_workspace_guard.py`: unit tests for boundary and manifest validation.
- `01_Tools/febio-tools/policies/root_AGENTS.md`: versioned source for the deployed workspace rule.
- `01_Tools/febio-tools/policies/tool_AGENTS.md`: versioned source for tool-development rules.
- `01_Tools/febio-tools/policies/cae_AGENTS.md`: versioned source for CAE rules.
- `01_Tools/febio-tools/docs/workspace-organization/`: approved design, plan, and migration report.

### Deployed workspace controls

- `AGENTS.md`: task-routing rule read by every LLM at workspace entry.
- `README.md`: human-readable workspace map and lifecycle guide.
- `02_CAE/AGENTS.md`: CAE-specific no-Git and retention rules.
- `02_CAE/NO_GIT.md`: visible no-Git sentinel.
- `02_CAE/<case>/CASE_MANIFEST.json`: machine-readable case state and file hashes.
- `02_CAE/98_Delete_Review/<date>/DELETE_CANDIDATES.csv`: later-deletion review ledger.
- `02_CAE/05_Verification` or migration evidence: solver and migration verification.

---

### Task 1: Freeze and Capture the Live Source State

**Files:**
- Create: `C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730\evidence\pre\inventory.csv`
- Create: `C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730\evidence\pre\git-show-ref.txt`
- Create: `C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730\evidence\pre\git-worktrees.txt`
- Create: `C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730\evidence\pre\git-status.txt`
- Create: `C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730\evidence\pre\main-dirty.patch`
- Create: `C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730\evidence\pre\worktree-state.json`
- Create: `C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730\evidence\pre\permanent-files-sha256.csv`
- Create: `C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730\legacy-repo.bundle`

**Interfaces:**
- Consumes: current root repository, all linked worktrees, and all workspace files.
- Produces: an immutable pre-migration evidence set used by Tasks 2, 6, and 8.

- [ ] **Step 1: Resolve and validate the two allowed roots**

Run in PowerShell:

```powershell
$workspace = [IO.Path]::GetFullPath('C:\Users\backo\OneDrive\Documents\FEBio')
$staging = [IO.Path]::GetFullPath('C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730')
if ($workspace -ne 'C:\Users\backo\OneDrive\Documents\FEBio') { throw 'Unexpected workspace path' }
if ($staging -ne 'C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730') { throw 'Unexpected staging path' }
New-Item -ItemType Directory -Force -Path "$staging\evidence\pre" | Out-Null
```

Expected: both paths match exactly and the evidence directory exists.

- [ ] **Step 2: Capture Git refs, status, worktrees, and dirty patches**

Run:

```powershell
git -C $workspace show-ref | Out-File -LiteralPath "$staging\evidence\pre\git-show-ref.txt" -Encoding utf8
git -C $workspace worktree list --porcelain | Out-File -LiteralPath "$staging\evidence\pre\git-worktrees.txt" -Encoding utf8
git -C $workspace status --porcelain=v2 --branch | Out-File -LiteralPath "$staging\evidence\pre\git-status.txt" -Encoding utf8
git -C $workspace diff --binary --output="$staging\evidence\pre\main-dirty.patch"
git -C $workspace bundle create "$staging\legacy-repo.bundle" --all
git -C $workspace bundle verify "$staging\legacy-repo.bundle"
```

Expected: bundle verification reports every included ref and exits 0.

- [ ] **Step 3: Capture every worktree's branch, HEAD, status, patch, and untracked paths**

Use `git worktree list --porcelain` to obtain exact paths. For each path, write:

```powershell
git -C $worktreePath status --porcelain=v2 --branch
git -C $worktreePath rev-parse HEAD
git -C $worktreePath diff --binary --output="$evidenceName.patch"
git -C $worktreePath ls-files --others --exclude-standard
```

Serialize the exact worktree path, branch, HEAD, status lines, patch path, and
untracked paths into `worktree-state.json`. Do not archive ignored caches yet;
the complete old worktree directories remain protected in Task 6.

- [ ] **Step 4: Inventory files and hash protected CAE content**

Create `inventory.csv` with:

```text
relative_path,size_bytes,last_write_time_utc,classification
```

Hash every existing `.fsm`, `.feb`, `.xplt`, `.step`, final `.html`, final
validation `.json`, and every dirty or untracked worktree file into
`permanent-files-sha256.csv` with:

```text
relative_path,size_bytes,sha256
```

- [ ] **Step 5: Prove the source state is stable**

Capture `show-ref`, `worktree list`, and `status` a second time after the
inventory. Compare the second outputs byte-for-byte with Step 2. If they
differ, discard neither capture, label the first one stale, and repeat Steps
2-5 until two consecutive captures match. Do not begin moves while the state
is changing.

---

### Task 2: Build the Staged Tool Repository and Workspace Guard

**Files:**
- Create: `C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730\febio-tools\`
- Create: `scripts/workspace/workspace_guard.py`
- Create: `tests/workspace/test_workspace_guard.py`

**Interfaces:**
- Consumes: `legacy-repo.bundle` and the approved case schema.
- Produces:
  - `find_git_markers(root: Path) -> list[Path]`
  - `validate_case(case_dir: Path) -> list[str]`
  - `validate_cae_root(cae_root: Path) -> list[str]`
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Clone the full local history without hardlinks**

Run:

```powershell
git clone --no-hardlinks "$staging\legacy-repo.bundle" "$staging\febio-tools"
git -C "$staging\febio-tools" show-ref
git -C "$staging\febio-tools" tag --list
```

Compare source branch heads from Task 1 with the clone's local or
`refs/remotes/origin/*` equivalents. Create a local branch for each source
branch needed by a retained worktree.

- [ ] **Step 2: Write failing boundary and manifest tests**

Create `tests/workspace/test_workspace_guard.py` containing tests equivalent to:

```python
import json
from pathlib import Path

from scripts.workspace.workspace_guard import (
    find_git_markers,
    validate_cae_root,
    validate_case,
)


def test_find_git_markers_detects_directory_and_pointer(tmp_path: Path):
    (tmp_path / "a" / ".git").mkdir(parents=True)
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / ".git").write_text("gitdir: C:/repo/.git/worktrees/b")
    assert {p.relative_to(tmp_path).as_posix() for p in find_git_markers(tmp_path)} == {
        "a/.git",
        "b/.git",
    }


def test_validate_case_accepts_active_case_with_required_schema(tmp_path: Path):
    case = tmp_path / "2026-07-30_Project_Case_Purpose_r01"
    for name in (
        "01_Input", "02_Model", "03_Result", "04_Report",
        "05_Verification", "90_Temporary",
    ):
        (case / name).mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "analysis_id": case.name,
        "project": "Project",
        "status": "active",
        "git_prohibited": True,
        "source_files": [],
        "model_files": [],
        "result_files": [],
        "report_files": [],
        "verification_files": [],
        "tool_versions": [],
        "solver": {"status": "not-run"},
        "temporary_status": "present",
    }
    (case / "CASE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert validate_case(case) == []


def test_validate_cae_root_rejects_git_marker(tmp_path: Path):
    (tmp_path / "01_Active" / "case" / ".git").mkdir(parents=True)
    assert validate_cae_root(tmp_path) == [
        "Git marker found: 01_Active/case/.git"
    ]
```

- [ ] **Step 3: Run the focused test and verify red**

Run:

```powershell
python -m pytest tests/workspace/test_workspace_guard.py -v
```

Expected: collection fails because `scripts.workspace.workspace_guard` does not
exist.

- [ ] **Step 4: Implement the minimal guard**

Create `scripts/workspace/workspace_guard.py` with:

```python
from __future__ import annotations

import json
from pathlib import Path

REQUIRED_CASE_DIRS = (
    "01_Input",
    "02_Model",
    "03_Result",
    "04_Report",
    "05_Verification",
    "90_Temporary",
)


def find_git_markers(root: Path) -> list[Path]:
    root = root.resolve()
    return sorted(
        (path for path in root.rglob(".git") if path.is_dir() or path.is_file()),
        key=lambda path: path.as_posix(),
    )


def validate_case(case_dir: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = case_dir / "CASE_MANIFEST.json"
    if not manifest_path.is_file():
        return ["Missing CASE_MANIFEST.json"]
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"Invalid CASE_MANIFEST.json: {exc}"]
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if data.get("analysis_id") != case_dir.name:
        errors.append("analysis_id must match the case directory")
    if data.get("status") not in {"active", "archived", "needs-review"}:
        errors.append("status must be active, archived, or needs-review")
    if data.get("git_prohibited") is not True:
        errors.append("git_prohibited must be true")
    for name in REQUIRED_CASE_DIRS:
        if not (case_dir / name).is_dir():
            errors.append(f"Missing directory: {name}")
    return errors


def validate_cae_root(cae_root: Path) -> list[str]:
    errors = [
        f"Git marker found: {path.relative_to(cae_root).as_posix()}"
        for path in find_git_markers(cae_root)
    ]
    return errors


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("cae_root", type=Path)
    args = parser.parse_args(argv)
    errors = validate_cae_root(args.cae_root)
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add empty `__init__.py` files under `scripts`, `scripts/workspace`, `tests`, and
`tests/workspace`.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/workspace/test_workspace_guard.py -v
git add scripts tests
git commit -m "feat: add FEBio workspace guard"
```

Expected: all three tests pass and the commit contains only guard code and its
tests.

---

### Task 3: Restructure and Verify Reusable Tool Content

**Files:**
- Move: `febio_gmsh_launcher/` to `apps/febio_gmsh_launcher/`
- Create: `scripts/tet10/`
- Create: `scripts/post/`
- Move from source workspace: `step_tet10_pipeline.py`
- Move from source workspace: `tet10_quality_artifacts.py`
- Move from source workspace: `febio_vm_fos_post.py`
- Move from source workspace: `tests/test_tet10_quality_artifacts.py`
- Move from source workspace: `tests/test_febio_vm_fos_post.py`
- Modify: tool tests only as required for new import paths
- Modify: `.gitignore`

**Interfaces:**
- Consumes: reusable files from the source root and existing launcher tests.
- Produces: a tool-only current tree with passing focused tests.

- [ ] **Step 1: Move the launcher with Git history**

Run in the staged clone:

```powershell
New-Item -ItemType Directory -Force -Path apps | Out-Null
git mv febio_gmsh_launcher apps/febio_gmsh_launcher
```

- [ ] **Step 2: Copy reusable untracked scripts into their final paths**

Create `scripts/tet10` and `scripts/post`, then copy:

```text
step_tet10_pipeline.py -> scripts/tet10/step_tet10_pipeline.py
tet10_quality_artifacts.py -> scripts/tet10/tet10_quality_artifacts.py
febio_vm_fos_post.py -> scripts/post/febio_vm_fos_post.py
```

Copy the two root tests into the staged clone. Update their imports to:

```python
from scripts.tet10 import tet10_quality_artifacts
from scripts.post import febio_vm_fos_post
```

Preserve the original root files until Task 7 moves them into the migration
backup.

- [ ] **Step 3: Run moved tests red, repair imports, and run green**

Run before import edits:

```powershell
python -m pytest tests/test_tet10_quality_artifacts.py tests/test_febio_vm_fos_post.py -v
```

Expected: import failure caused by the new module locations.

After import edits, run the same command and expect all tests to pass.

- [ ] **Step 4: Run launcher tests from their own project directory**

Run:

```powershell
Set-Location apps\febio_gmsh_launcher
python -m pytest tests -v
```

Expected: the launcher suite passes or has only its previously established
skips. Classify dependency-only failures separately and do not claim success
from a partial run.

- [ ] **Step 5: Install a tool-only `.gitignore`**

Include:

```gitignore
__pycache__/
*.py[cod]
.pytest_cache/
.venv/
build/
dist/
*.egg-info/
*.log
tmp/
```

Do not add patterns for real CAE formats because real CAE data must not enter
this repository at all.

- [ ] **Step 6: Commit the tool restructuring**

Run:

```powershell
git add .gitignore apps scripts tests
git commit -m "refactor: organize reusable FEBio tools"
```

Expected: no `.fsm`, real-model `.feb`, `.xplt`, STEP, MSH, INP, VTU, or VOL
file is staged.

---

### Task 4: Install Versioned and Deployed LLM Policies

**Files:**
- Create: `01_Tools/febio-tools/policies/root_AGENTS.md`
- Create: `01_Tools/febio-tools/policies/tool_AGENTS.md`
- Create: `01_Tools/febio-tools/policies/cae_AGENTS.md`
- Create: `AGENTS.md`
- Create: `README.md`
- Create: `02_CAE/AGENTS.md`
- Create: `02_CAE/NO_GIT.md`

**Interfaces:**
- Consumes: approved routing, retention, archive, and deletion rules.
- Produces: human and LLM controls that remain readable outside Git.

- [ ] **Step 1: Write the policy templates**

Write `policies/root_AGENTS.md` with:

```markdown
# FEBio workspace rules

Before any work, classify the task.

- Reusable software, tests, installers, and tool documentation belong under
  `01_Tools/febio-tools` and use Git.
- Real-model preprocessing, meshing, solving, post-processing, and reporting
  belong under `02_CAE` and must never use Git or GitHub.
- Do not place new files at the workspace root.
- Read the nearest nested `AGENTS.md` before modifying either area.
- Never delete an existing CAE file unless it is listed in an approved
  `DELETE_CANDIDATES.csv`.
```

Write `policies/tool_AGENTS.md` with:

```markdown
# Tool-development rules

- This is the only Git repository in the FEBio workspace.
- Use Git for every reusable tool change.
- Never copy a real CAE model, result, input STEP, FSM, FEB, XPLT, MSH, INP,
  VTU, or VOL into this repository.
- Tests may use only small synthetic fixtures with no product geometry.
- Tool outputs for a real analysis must resolve under that case's
  `90_Temporary`.
- Run focused tests and inspect Git status before every commit.
```

Write `policies/cae_AGENTS.md` with:

```markdown
# CAE rules

- Git and GitHub are prohibited everywhere below `02_CAE`.
- Stop immediately if `git rev-parse --is-inside-work-tree` succeeds or a
  `.git` marker exists in this tree.
- Every analysis belongs to one case under `01_Active`.
- Store source input in `01_Input`, FSM and FEB in `02_Model`, XPLT in
  `03_Result`, final reports in `04_Report`, and hashes, solver evidence, and
  validation in `05_Verification`.
- Store MSH, INP, VTU, VOL, candidates, trial logs, caches, and temporary
  scripts only in `90_Temporary` or `99_Temporary`.
- Move a case to `02_Archive` only after the completion gate in the workspace
  README passes.
- Never delete a file directly. Move reviewed candidates to
  `98_Delete_Review` and wait for explicit user approval.
```

- [ ] **Step 2: Deploy identical policy copies**

Copy the templates to:

```text
policies/root_AGENTS.md -> FEBio/AGENTS.md
policies/tool_AGENTS.md -> staged febio-tools/AGENTS.md
policies/cae_AGENTS.md -> FEBio/02_CAE/AGENTS.md
```

Record each deployed copy's SHA-256 in the migration report.

- [ ] **Step 3: Create the root README and no-Git sentinel**

`README.md` must show the target tree, explain Active versus Archive, list
permanent formats, and state that the first cleanup has not deleted data.
`NO_GIT.md` must state that detection of Git metadata below `02_CAE` is a hard
stop.

- [ ] **Step 4: Validate policy text and commit templates**

Search the templates for all required terms:

```powershell
rg -n "01_Tools|02_CAE|Git|GitHub|90_Temporary|02_Archive|SHA-256" policies
```

Commit only versioned tool-repository copies:

```powershell
git add AGENTS.md policies
git commit -m "docs: define FEBio workspace policy"
```

---

### Task 5: Create CAE Cases and Migrate Root CAE Data

**Files:**
- Create: `02_CAE/00_Shared/Materials/`
- Create: `02_CAE/01_Active/Bottom_Frame/2026-07-28_0728C_tet10-preparation/`
- Create: `02_CAE/90_Needs_Review/Bottom_Frame/2026-07-24_0724A_repair-trial/`
- Create: `02_CAE/98_Delete_Review/2026-07-30/`
- Create: `02_CAE/99_Temporary/Legacy_tmp/`
- Create: case `README.md` and `CASE_MANIFEST.json` files
- Move: `material_library/`
- Move: 0724_A root files
- Move and split: `02_Bottom_Frame_FEBio_CAD/`
- Move and split: `02_Bottom_Frame_Tet10/`
- Move: `tmp/`, root caches, and empty `test-output/`

**Interfaces:**
- Consumes: pre-migration hash manifest and existing CAE directories.
- Produces: explicit Active, Needs Review, Delete Review, Shared, and Temporary content.

- [ ] **Step 1: Create all case subdirectories**

For each case create:

```text
01_Input
02_Model
03_Result
04_Report
05_Verification
90_Temporary
```

- [ ] **Step 2: Move the 0724_A trial**

Run with `$workspace` and `$case0724` set to their exact absolute paths:

```powershell
Move-Item -LiteralPath "$workspace\02_Bottom Frame_v1.0,0724_A_repaired_candidate.step" `
  -Destination "$case0724\01_Input\02_Bottom Frame_v1.0,0724_A_repaired_candidate.step"
Move-Item -LiteralPath "$workspace\02_Bottom Frame_v1.0,0724_A_repair_report.md" `
  -Destination "$case0724\04_Report\02_Bottom Frame_v1.0,0724_A_repair_report.md"
Move-Item -LiteralPath "$workspace\02_Bottom Frame_v1.0,0724_A_gmsh_test_only.msh" `
  -Destination "$case0724\90_Temporary\02_Bottom Frame_v1.0,0724_A_gmsh_test_only.msh"
```

Set manifest status to `needs-review` and record that FSM, FEB, and XPLT are
absent.

- [ ] **Step 3: Split and move the 0728_C data**

Move these exact adopted/permanent candidates first:

```text
02_Bottom_Frame_FEBio_CAD/02_Bottom Frame_v1.0,0728_C_FEBio_repaired.step
  -> 01_Input
02_Bottom_Frame_Tet10/02_Bottom Frame_v1.0,0728_C_Tet10.fsm
  -> 02_Model
02_Bottom_Frame_FEBio_CAD/FEBio_CAD_Tet10_repair_report.html
  -> 04_Report
02_Bottom_Frame_FEBio_CAD/README_JA.md
  -> 04_Report
02_Bottom_Frame_Tet10/README_ja.md
  -> 04_Report
02_Bottom_Frame_FEBio_CAD/FEBio_mesh_settings.json
  -> 05_Verification
02_Bottom_Frame_FEBio_CAD/final_verification.json
  -> 05_Verification
02_Bottom_Frame_Tet10/DELIVERY/validation_report.json
  -> 05_Verification
02_Bottom_Frame_Tet10/DELIVERY/SHA256SUMS.txt
  -> 05_Verification
02_Bottom_Frame_Tet10/DELIVERY/validate_tet10_delivery.py
  -> 05_Verification/Legacy_Scripts
```

Move the identical second FSM to:

```text
02_CAE/98_Delete_Review/2026-07-30/Duplicates/
```

Move the different `~...auto.fsm` to its own Needs Review case. Never classify
it as a duplicate.

After all listed files move, move the entire residual source directories to:

```text
90_Temporary/legacy_02_Bottom_Frame_FEBio_CAD
90_Temporary/legacy_02_Bottom_Frame_Tet10
```

This captures every remaining candidate, mesh, conversion output, duplicated
script, and log without deletion or omission.

- [ ] **Step 4: Move shared and temporary directories**

Move `material_library` to `00_Shared/Materials`. Move `tmp` to
`99_Temporary/Legacy_tmp`. Move caches and the empty output directory to
`98_Delete_Review/2026-07-30`, retaining their original relative path in the
delete ledger.

- [ ] **Step 5: Generate case manifests and verify hashes**

Populate every manifest file entry with original path, new relative path,
role, byte size, and SHA-256. Compare all protected hashes with Task 1.

Run:

```powershell
python "$staging\febio-tools\scripts\workspace\workspace_guard.py" `
  "C:\Users\backo\OneDrive\Documents\FEBio\02_CAE"
```

Expected: exit 0 and no Git-marker errors.

---

### Task 6: Migrate Reports, LLM History, and Preserve Worktrees

**Files:**
- Create: five 0729_C and later Active case folders
- Move: reports to case `04_Report`
- Move: CAE designs and plans to case `05_Verification/LLM_History`
- Create: `01_Tools/_migration_backup/legacy-worktrees/`
- Create: recreated tool worktrees under `01_Tools/_worktrees/`

**Interfaces:**
- Consumes: live worktree snapshot, dirty patches, untracked-path lists, and CAE report mapping.
- Produces: preserved tool worktrees and non-Git CAE history.

- [ ] **Step 1: Group every existing report**

Move local-refinement HTML/JSON, three-case comparison HTML/JSON, fatigue
report, VM/FOS documents, contact documents, and local040 documents into their
approved cases. Record external `C:\dev\FEBio` paths as references only.

- [ ] **Step 2: Preserve CAE-only branch content**

For clean CAE worktrees, copy the relevant report/design/plan artifacts into
the matching case and record branch plus commit in `LLM_History/README.md`.
Keep the branch in the new repository history but do not recreate a live CAE
worktree.

- [ ] **Step 3: Remove CAE documents from the tool repository's current tree**

After the current workspace versions have been copied into their CAE cases,
run `git rm` in the staged repository for the Bottom Frame analysis-only plans
and specifications. Retain only documents whose filenames contain:

```text
fusion-step-repair-monitor
febio-gmsh-launcher
febio-workspace-organization
```

under the tool repository's current `docs/superpowers` tree. Commit:

```powershell
git add docs
git commit -m "chore: move CAE documents out of tool repository"
```

- [ ] **Step 4: Back up complete old worktree directories**

Resolve every old worktree directory and the backup target beneath the
workspace. Move each complete old directory to
`01_Tools/_migration_backup/legacy-worktrees` only after Task 1 evidence has
been verified and no writing process remains.

- [ ] **Step 5: Recreate tool-development worktrees**

Create linked worktrees from the staged/final tool repository for the
tool-development branches. Restore each modified tracked file and untracked
file from its complete backup.

- [ ] **Step 6: Compare dirty state exactly**

Generate a binary diff for each recreated worktree and compare it with the
Task 1 patch. Compare every listed untracked file by SHA-256. A mismatch stops
the migration and leaves the complete legacy worktree backup untouched.

---

### Task 7: Activate the Physical Git Boundary

**Files:**
- Move staged repository to: `01_Tools/febio-tools/`
- Move old root Git data to: `01_Tools/_migration_backup/legacy-root-git/`
- Move mixed legacy working-tree remnants to: `01_Tools/_migration_backup/legacy-root-working-tree/`

**Interfaces:**
- Consumes: verified staged repository and migrated CAE tree.
- Produces: root and CAE outside Git, with only the tool repository active.

- [ ] **Step 1: Recheck live source state**

Compare current refs, status, worktree paths, and dirty patches to Task 1. If
anything changed after the stable snapshot, incorporate and reverify the new
state before activation.

- [ ] **Step 2: Move the old Git administrative directory into backup**

Resolve:

```text
source: C:\Users\backo\OneDrive\Documents\FEBio\.git
target: C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\_migration_backup\legacy-root-git
```

Verify both are beneath the workspace, the source is exactly `.git`, and the
target does not exist before `Move-Item -LiteralPath`.

- [ ] **Step 3: Move the staged repository into its final location**

Move:

```text
C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730\febio-tools
-> C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\febio-tools
```

Run `git worktree repair` for recreated tool worktrees if their administrative
paths changed.

- [ ] **Step 4: Move residual legacy root files into classified backup**

Any original reusable script already copied into the tool repository moves to
`legacy-root-working-tree/tools`. Any mixed historical `docs` snapshot moves
to `legacy-root-working-tree/docs`. No source file is removed.

- [ ] **Step 5: Remove the staging-only remote**

If `origin` points to `legacy-repo.bundle` or another staging path, remove it:

```powershell
git -C 'C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\febio-tools' remote remove origin
```

Do not push the legacy mixed CAE history. Record the absence of a safe GitHub
remote in the migration report; a filtered tool-only GitHub publication is
outside this filesystem migration.

- [ ] **Step 6: Prove the no-Git boundary**

Run:

```powershell
git -C 'C:\Users\backo\OneDrive\Documents\FEBio' rev-parse --is-inside-work-tree
git -C 'C:\Users\backo\OneDrive\Documents\FEBio\02_CAE' rev-parse --is-inside-work-tree
Get-ChildItem -LiteralPath 'C:\Users\backo\OneDrive\Documents\FEBio\02_CAE' `
  -Recurse -Force -Filter .git
git -C 'C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\febio-tools' status --short --branch
```

Expected: the first two Git commands fail, no `.git` marker is found below
`02_CAE`, and the final tool repository reports a valid branch.

---

### Task 8: Produce Deletion Review and Final Migration Evidence

**Files:**
- Create: `02_CAE/98_Delete_Review/2026-07-30/DELETE_CANDIDATES.csv`
- Create: `01_Tools/febio-tools/docs/workspace-organization/migration-verification.md`
- Create: `C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730\evidence\post\inventory.csv`
- Create: `C:\Users\backo\OneDrive\Documents\FEBio__migration_staging_20260730\evidence\post\verification.json`

**Interfaces:**
- Consumes: pre-migration evidence, final workspace, final Git repo, and case manifests.
- Produces: auditable proof of preservation and a non-destructive cleanup proposal.

- [ ] **Step 1: Generate the delete candidate ledger**

Use columns:

```text
relative_path,size_bytes,sha256,classification,reason,
protected_by_manifest,proposed_at,status
```

All rows have `status=quarantined`. Include only caches, confirmed duplicates,
empty output directories, and files already placed in Temporary or Delete
Review. Do not delete any row.

- [ ] **Step 2: Reconcile pre/post inventories**

Every pre-migration file must match exactly one of:

- same path and hash;
- move-ledger destination and matching hash;
- complete migration backup path and matching hash.

Record unmatched, multiply matched, size-mismatched, and hash-mismatched counts.
Every count must be zero.

- [ ] **Step 3: Verify Git refs and dirty worktrees**

Compare branch heads and tags with Task 1. Verify the final tool repository's
targeted tests again. Verify every recreated dirty tool worktree patch and
untracked hash.

- [ ] **Step 4: Verify CAE policy and manifests**

Run the workspace guard against `02_CAE`. Parse every `CASE_MANIFEST.json`.
Re-hash the canonical FSM, shared material FEB, final reports, and all files
listed as permanent or Needs Review.

- [ ] **Step 5: Write and commit the migration report**

The report records:

- final folder paths and sizes;
- Git boundary results;
- source and final branch heads;
- dirty-worktree preservation results;
- tool test results;
- case validation results;
- canonical FSM hash;
- delete-review total size;
- no-deletion statement;
- migration-backup path; and
- any remaining external-reference or GitHub-remote limitation.

Commit only the report and any final tool-policy changes:

```powershell
git add docs/workspace-organization policies scripts tests
git commit -m "docs: record FEBio workspace migration"
```

- [ ] **Step 6: Run final verification**

Run:

```powershell
python -m pytest tests/workspace/test_workspace_guard.py -v
python -m pytest tests/test_tet10_quality_artifacts.py tests/test_febio_vm_fos_post.py -v
python -m pytest apps/febio_gmsh_launcher/tests -v
git status --short --branch
```

Report exact pass, skip, and failure counts. Do not remove
`_migration_backup`, the sibling evidence directory, or any quarantined file.
