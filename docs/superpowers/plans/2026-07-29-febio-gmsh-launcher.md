# FEBio Gmsh Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FEBio Studio 3.1 の Local Launch Configuration から、CAD 面選択を保持した参照 FEB を Gmsh 曲面 Tet10 へ置換し、FEBio G8 Jacobian 品質ゲート後に FEBio 4.12 をヘッドレス実行して、元ジョブ名の XPLT を Studio で開ける Windows ランチャーを完成させる。

**Architecture:** Python ランチャーが Studio の `-i $(Filename)` 契約を受け、モデル側 JSON、参照 FEB、STEP を解決する。参照 FEB の Surface/Part を STEP の OCC entity へ幾何照合し、entity 分類を維持した Gmsh Tet10 を作る。FEBio と同じ G8 積分点で全要素を検査し、必要時だけ境界中間節点の曲率を緩和する。非 Mesh セクションを保持した解析 FEB を一時生成し、FEBio を子プロセス実行して元ジョブ名の `.log` と `.xplt` を作る。

**Tech Stack:** Python 3.12、gmsh 4.15.2、NumPy 2.5.1、標準ライブラリ XML/Tkinter/subprocess、pytest 9、PyInstaller 6、FEBio Studio 3.1、FEBio 4.12

**Source contract:** FEBio Studio v3.1 `CLocalJobProcess::run()` は Local Launch Configuration の executable をジョブ FEB のフォルダで起動し、既定引数 `-i $(Filename)` を渡す。終了コード 0 の後、`CFEBioJob::GetPlotFileName()` が指す同一 basename の `.xplt` を `OpenPostFile()` で開く。ランチャーはこの契約を変更しない。

**Global constraints:**

- 実装仕様は `docs/superpowers/specs/2026-07-29-febio-gmsh-launcher-design.md` を正とする。
- 境界条件や荷重が参照する Surface と、Material が参照する Part/Domain のみを初期転写対象とする。
- edge、vertex、node、discrete mesh 選択は検出した時点で明示的に失敗させる。
- `det(J) <= 0` の Tet10 を一つでも残して FEBio を起動しない。
- 参照 FEB、STEP、既存 XPLT、既存 LOG を上書きしない。最終出力は一時ファイルから原子的に昇格する。
- Bottom Frame の完全解析は既知のメモリ規模が大きいため、回帰はメッシュ転写・全要素品質ゲート・FEBio 初期化完了までを必須とし、解法完走は小型モデルで検証する。

---

## Task 1: Package scaffold and FEBio-compatible CLI

**Files:**

- Create: `febio_gmsh_launcher/pyproject.toml`
- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/__init__.py`
- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/cli.py`
- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/errors.py`
- Create: `febio_gmsh_launcher/tests/test_cli.py`

- [ ] Write failing CLI tests for `-i`, quoted Windows paths, passthrough FEBio flags, missing input, and stable exit codes.
- [ ] Run `pytest tests/test_cli.py -q` and confirm failure.
- [ ] Implement `LaunchRequest`, `parse_febio_args(argv)`, and error categories `CONFIG_ERROR=20`, `PREFLIGHT_CANCEL=21`, `TRANSFER_ERROR=30`, `QUALITY_ERROR=40`, `SOLVER_ERROR=50`, `INTERNAL_ERROR=70`.
- [ ] Add console entry point `febio-gmsh-launcher = febio_gmsh_launcher.cli:main`.
- [ ] Run the CLI tests and commit only Task 1 files.

## Task 2: Model configuration and deterministic run paths

**Files:**

- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/config.py`
- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/artifacts.py`
- Create: `febio_gmsh_launcher/tests/test_config.py`
- Create: `febio_gmsh_launcher/tests/test_artifacts.py`

- [ ] Write failing tests for explicit `--config`, same-directory lookup, three-parent lookup, ambiguity rejection, model stem mismatch, SHA-256 mismatch, and path traversal rejection.
- [ ] Write failing tests for unique run directories and atomic promotion to `<job>.xplt`/`<job>.log` without overwriting a pre-existing successful result before completion.
- [ ] Implement typed `RunConfig` parsing and `resolve_config(input_feb, explicit, max_parents=3)`.
- [ ] Implement `RunArtifacts.create(job_feb)` with `<job>.gmsh-runs/<UTC>-<short-id>/` and atomic `promote_success()`.
- [ ] Run tests and commit Task 2 files.

## Task 3: Streaming reference FEB reader and dependency inventory

**Files:**

- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/febio_xml.py`
- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/model.py`
- Create: `febio_gmsh_launcher/tests/fixtures/reference_tet4.feb`
- Create: `febio_gmsh_launcher/tests/fixtures/unsupported_node_selection.feb`
- Create: `febio_gmsh_launcher/tests/test_febio_xml.py`

- [ ] Write a minimal Tet4 fixture containing one SolidDomain, two named Surfaces, a global fixed BC, and a step surface load.
- [ ] Write failing tests that extract nodes, Tet4 elements, surface triangles, domains, and every component reference without loading unrelated numeric sections.
- [ ] Write a failing test that reports unsupported node/edge/vertex selections with the owning component name.
- [ ] Implement streaming `scan_reference_feb(path) -> ReferenceModel` using `xml.etree.ElementTree.iterparse` and aggressive element clearing.
- [ ] Implement `required_surface_names()` and `required_domain_names()` from component attributes including `surface`, `node_set="@surface:..."`, `elem_set`, `domain`, `primary`, and `secondary`.
- [ ] Run tests and commit Task 3 files.

## Task 4: FEBio/Gmsh high-order node ordering and G8 Jacobian gate

**Files:**

- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/ordering.py`
- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/quality.py`
- Create: `febio_gmsh_launcher/tests/test_ordering.py`
- Create: `febio_gmsh_launcher/tests/test_quality.py`

- [ ] Write failing permutation tests for Gmsh type-11 Tet10 to FEBio Tet10 and Gmsh type-9 Tri6 to FEBio Tri6.
- [ ] Write failing analytical tests for a straight unit Tet10, an inverted corner Tet10, and a curved-midnode Tet10 that is positive at corners but negative at a G8 point.
- [ ] Implement explicit ordering tables, Tet10 quadratic shape derivatives, the eight FEBio integration points, chunked determinant evaluation, and `QualityReport`.
- [ ] Implement `assert_quality_gate()` requiring positive corner volume, positive all-G8 determinant, finite coordinates, and configurable determinant floor.
- [ ] Run tests and commit Task 4 files.

## Task 5: STEP import, entity inventory, and reference-selection mapping

**Files:**

- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/gmsh_session.py`
- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/selection_transfer.py`
- Create: `febio_gmsh_launcher/tests/test_selection_transfer.py`
- Create: `febio_gmsh_launcher/tests/integration/test_step_entity_mapping.py`
- Create: `febio_gmsh_launcher/tests/fixtures/two_face_block.step`

- [ ] Generate and commit a tiny deterministic STEP fixture with distinguishable top/bottom faces.
- [ ] Write failing pure-geometry tests for reference-triangle centroid/normal/bbox signatures and ambiguity detection.
- [ ] Write a failing integration test that maps named reference Surface triangles onto OCC surface entities and preserves separate top/bottom groups.
- [ ] Implement a context-managed Gmsh session and `import_step()` with OCC synchronize, solid validation, and entity geometry inventory.
- [ ] Implement candidate pruning by expanded bbox, `gmsh.model.getClosestPoint`, normal alignment, sampled coverage, and area conservation.
- [ ] Reject missing, ambiguous, overlapping, or tolerance-violating mappings before meshing.
- [ ] Run unit and integration tests and commit Task 5 files.

## Task 6: Curved Tet10 meshing and local curvature relaxation

**Files:**

- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/mesher.py`
- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/curvature.py`
- Create: `febio_gmsh_launcher/tests/test_curvature.py`
- Create: `febio_gmsh_launcher/tests/integration/test_mesher.py`

- [ ] Write failing tests for the maximal admissible relaxation factor `alpha`, shared midside-node consistency, correction-limit failure, and final full-mesh recheck.
- [ ] Write an integration test that creates physical volume/surface groups, generates Tet4, applies Netgen optimization, elevates to order 2, and applies `HighOrder` plus `HighOrderElastic`.
- [ ] Implement `GmshMesher.mesh(step, mapped_entities, config) -> MeshData` retaining node tags, Tet10, boundary Tri6, volume entity tags, and surface entity tags.
- [ ] Implement local midside-node relaxation toward straight edge midpoints using a monotone binary search over connected invalid elements.
- [ ] Enforce correction count/fraction, minimum alpha, and displacement limits from config; then rerun the full G8 gate.
- [ ] Run tests and commit Task 6 files.

## Task 7: FEB translator preserving analysis semantics

**Files:**

- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/translator.py`
- Create: `febio_gmsh_launcher/tests/test_translator.py`
- Create: `febio_gmsh_launcher/tests/fixtures/reference_semantics.feb`

- [ ] Write failing tests that replace only `<Mesh>` and `<MeshDomains>`, convert Tet10/Tri6 ordering, preserve Material/Boundary/Loads/Contact/Step/Output exactly, and retain all referenced set names.
- [ ] Write failing tests for a missing mapped set, duplicate node tag, orphan face node, and domain/material mismatch.
- [ ] Implement streaming top-level FEB rewrite to a temporary file without constructing a full giant XML tree.
- [ ] Emit Nodes, Tet10 Elements per domain, Tri6 Surfaces, and SolidDomain entries with deterministic IDs and original names.
- [ ] Re-scan the generated FEB and enforce reference closure before atomic rename to the run artifact.
- [ ] Run tests and commit Task 7 files.

## Task 8: Preflight, log window, orchestration, and cancellation

**Files:**

- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/orchestrator.py`
- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/ui.py`
- Create: `febio_gmsh_launcher/src/febio_gmsh_launcher/solver.py`
- Create: `febio_gmsh_launcher/tests/test_orchestrator.py`
- Create: `febio_gmsh_launcher/tests/test_solver.py`

- [ ] Write failing orchestration tests with fake mesher/translator/solver for success, cancel, config failure, transfer failure, quality failure, solver failure, and cleanup.
- [ ] Write failing solver tests for command construction, merged stdout/stderr streaming, process-tree cancellation, and nonzero exit propagation.
- [ ] Implement compact Tkinter preflight showing model, STEP hash status, sizes, mapped Surface/Part counts, solver path, and artifact folder.
- [ ] Implement a separate Tkinter log window with stage/progress, timestamped lines, warning/error highlighting, artifact path, and Cancel.
- [ ] Implement FEBio child execution and stream lines simultaneously to UI, run log, and Studio-captured stdout.
- [ ] Orchestrate snapshot -> transfer -> mesh -> quality -> translate -> solve -> promote; retain failed artifacts and return stable exit codes.
- [ ] Run tests and commit Task 8 files.

## Task 9: Small-model end-to-end analysis

**Files:**

- Create: `febio_gmsh_launcher/tests/e2e/make_small_case.py`
- Create: `febio_gmsh_launcher/tests/e2e/test_small_case.py`
- Create: `febio_gmsh_launcher/tests/e2e/small_case.gmsh-run.json`
- Create: `febio_gmsh_launcher/docs/small-model-e2e.md`

- [ ] Build a tiny STEP solid and reference Tet4 FEB with two independently named CAD surfaces, fixed BC, displacement load, material, and output request.
- [ ] Run the real Gmsh/FEBio pipeline non-interactively and assert exit 0, positive all-G8 Tet10 quality, Surface closure, nonempty `.log`, and nonempty `.xplt`.
- [ ] Open the generated XPLT headlessly where supported, otherwise verify FEBio normal termination and XPLT header/size.
- [ ] Record exact commands, versions, counts, minimum determinant, corrected-node count, and hashes.
- [ ] Commit the passing E2E fixture and evidence.

## Task 10: FEBio Studio Launch Configuration installer and argument contract

**Files:**

- Create: `febio_gmsh_launcher/scripts/build.ps1`
- Create: `febio_gmsh_launcher/scripts/install.ps1`
- Create: `febio_gmsh_launcher/scripts/uninstall.ps1`
- Create: `febio_gmsh_launcher/tests/test_packaged_cli.py`
- Create: `febio_gmsh_launcher/README.md`

- [ ] Write a packaged smoke test that invokes the executable exactly as Studio does: `FEBioGmshLauncher.exe -i Model.feb`.
- [ ] Package with PyInstaller and verify Gmsh Python package/DLL discovery on a clean child process.
- [ ] Implement idempotent install/uninstall scripts that copy the package to a user-writable directory and print the exact Studio 3.1 Local Launch Configuration path.
- [ ] Document that the Run dialog command remains the default `-i $(Filename)` and explain the `<model>.gmsh-run.json` placement/parent lookup.
- [ ] Verify successful exit leaves `<job>.xplt` and `<job>.log` at the paths Studio expects.
- [ ] Commit package scripts and documentation.

## Task 11: Bottom Frame regression and FEBio initialization

**Files:**

- Create: `febio_gmsh_launcher/config/examples/02_Bottom_Frame_FEBio_Tet10.gmsh-run.json`
- Create: `febio_gmsh_launcher/scripts/verify_bottom_frame.ps1`
- Create: `febio_gmsh_launcher/docs/bottom-frame-validation.md`

- [ ] Point the example config at `C:\Users\backo\Downloads\02_Bottom Frame_v1.0,0728_C.step`, pin its SHA-256, and select bounded mesh/quality thresholds based on the approved design.
- [ ] Export/use a Tet4 reference FEB from the FSM and inventory the required `ZeroDisplacement1`, `PushByScrew`, and `Part1` mappings.
- [ ] Run Gmsh curved Tet10 generation, selection transfer, and full all-G8 quality gate; archive JSON report and hashes.
- [ ] Start FEBio 4.12 headlessly on the translated file and capture evidence that model initialization passes without a negative Jacobian; stop before resource-heavy solve continuation if configured.
- [ ] Confirm generated FEB contains the original Surface names and every BC/load reference resolves.
- [ ] Document element/node counts, minimum G8 determinant, corrections, elapsed time, peak memory when available, and any bounded limitation.
- [ ] Commit only config, verification script, and markdown evidence; do not commit large generated meshes/results.

## Task 12: FEBio Studio real GUI smoke and completion handoff

**Files:**

- Modify: `febio_gmsh_launcher/README.md`
- Create: `febio_gmsh_launcher/docs/febio-studio-smoke.md`

- [ ] Register/select the packaged executable as a Local Launch Configuration in FEBio Studio 3.1.
- [ ] Run the small model from Studio with default `-i $(Filename)` and accept preflight.
- [ ] Verify the separate log window updates during meshing and solving, Cancel is responsive, and Studio receives the final exit status.
- [ ] Accept Studio's completed-job dialog and verify the result opens in the original Studio process with the expected model association.
- [ ] Repeat one controlled failure and verify no stale/partial XPLT is opened.
- [ ] Record screenshots or exact observed state, package hash, installed path, and reproducible steps.
- [ ] Run the complete pytest suite plus packaged smoke, review `git diff`, and commit final documentation.

## Final verification checklist

- [ ] `pytest -q` passes in the project virtual environment.
- [ ] Packaged CLI exact Studio invocation passes.
- [ ] Small curved Tet10 model completes in FEBio 4.12 and produces a readable XPLT.
- [ ] Bottom Frame translated model passes full G8 quality and FEBio initialization.
- [ ] Every referenced CAD Surface/Part has one validated mapping and its original FEBio name.
- [ ] Failed runs cannot replace a prior successful XPLT/LOG.
- [ ] FEBio Studio 3.1 opens the successful XPLT in the original process.
- [ ] README contains installation, model JSON, run, cancellation, diagnostics, and uninstall instructions.
