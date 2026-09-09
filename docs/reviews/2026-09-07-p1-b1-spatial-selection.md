# P1-B1 spatial and selection contract verification

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

Date: 2026-09-07
Scope: FEBio CAE Harness V2 P1-B1 spatial values and provenance-aware selection contracts
Decision boundary: this report covers the new domain contracts, local tests, static gates, package build, and installed-package smoke only.

This is synthetic/local evidence. It is not evidence of a real FEBio solve, official FBS, FEBio Studio, Gmsh, LLM, native-app, real-model, `02_CAE`, or BottomFrame success. No physical meaning is inferred from geometry or naming conventions; unresolved physical conditions remain outside this contract.

## Fixed Git boundary

| Item | Value |
|---|---|
| Working branch | `codex/p1-b1-spatial-selection` |
| P1-B1 base | `76176add3d270d7ca003f7bd09a61471f199beb5` |
| initial test-only API SHA | `922749aab84eb5244d47b0443317ed3c882c7ced` |
| initial test-only correction SHA | `40c1c4f303b70b3dcbddf70078cd794c9f418e13` |
| initial production SHA | `142442583394926e101d9915c462d8787ec8cba9` |
| R1 semantic test-only SHA | `5d2bd6cc3d22db2e583ffc4757ce66ab4b296e32` |
| R1 test-fixture correction SHA | `0d85ec64b77c85c1747cd8a947f137ab8cbd8f20` |
| R1 semantic production SHA | `a401c035b51634914b161acdfd9cf83b7b13dd0c` |
| final code candidate | `a401c035b51634914b161acdfd9cf83b7b13dd0c` |
| authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote state | `REMOTE_CONFIGURED` |
| locally observed `origin/V2` | `f903575283136ffe45ff0c703bc3f6fab3462285` |
| push/integration | not performed by this worker |

The initial test-only commits add or tighten only the P1-B1 contract tests. The initial production commit adds only `domain/spatial.py`, `domain/selection.py`, and the corresponding `domain/__init__.py` exports. R1 adds only regressions in the two existing contract test files, then fixes only `domain/spatial.py` and `domain/selection.py`; exports and shared canonical/unit/evidence owners are unchanged. The R1 candidate has no uncommitted tracked changes.

## Initial candidate RED / GREEN (superseded by R1)

All captured commands used `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe` (Python 3.12.10). The local runner records the expanded argv, cwd, interpreter, HEAD and dirty state before/after, UTC start/end, exit code, and absolute stdout/stderr paths under `.local/coordination/runs/`.

### API RED

At test-only SHA `922749aab84eb5244d47b0443317ed3c882c7ced`, `P1-B1-red-basetemp-preflight-01` verified that the fresh basetemp did not exist (exit 0). The exact test command was:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_spatial.py tests/unit/contracts/test_selection.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B1-red-01
```

`P1-B1-red-api-01` collected 22 tests, with 2 failed and 20 skipped, exit 1. Both failures were the intended API-availability assertions; there were no collection, import-setup, or synthetic-environment errors. Raw evidence is `.local/coordination/runs/P1-B1-red-api-01/{metadata.json,stdout.bin,stderr.bin}`.

### Clean final-candidate GREEN

At clean production SHA `142442583394926e101d9915c462d8787ec8cba9`, `P1-B1-green-clean-basetemp-preflight-01` verified a fresh basetemp (exit 0). The same focused command, recorded as `P1-B1-green-clean-01`, produced 22 passed and exit 0. Raw evidence is `.local/coordination/runs/P1-B1-green-clean-01/{metadata.json,stdout.bin,stderr.bin}`.

## Implemented contracts

- `src/febio_cae/domain/spatial.py` provides immutable opaque identifiers (`FrameId`, `GeometryId`, `BodyId`, and `FaceId`), frame-explicit `Point3` and `Translation3` length values, finite nonzero normalized `UnitDirection`, validated proper rotations, and `RigidTransform`. A translation must be expressed in the transform target frame; no default frame or implicit orientation is supplied.
- `src/febio_cae/domain/selection.py` provides explicit named-attribute, coordinate-predicate, face-set, and whole-body rules. Face and coordinate rules carry body/frame/geometry context, face selections carry `EvidenceRef` provenance, and `SelectionRef` rejects mismatched rule or resolution context.
- `FaceMeasurement` and `ResolutionSnapshot` capture immutable, frame-matched observations. Sequence inputs are copied, duplicate face identifiers are rejected, and optional resolution is explicit rather than inferred.
- Spatial and selection payloads use the existing schema-versioned canonical serializer. Quantity values are projected to SI for deterministic payloads, while physical selection meaning remains evidence-bound.

## Initial candidate local gates (superseded by R1)

All final gates below ran at the clean candidate SHA, with dirty state empty before and after. Each record retains exact command metadata and raw stdout/stderr.

| Record | Exact command | Result | Raw evidence |
|---|---|---|---|
| `P1-B1-gate-pytest-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B1-gate-pytest-01` | 99 passed, exit 0 | `.local/coordination/runs/P1-B1-gate-pytest-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-gate-format-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .` | 29 files formatted, exit 0 | `.local/coordination/runs/P1-B1-gate-format-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-gate-lint-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .` | all checks passed, exit 0 | `.local/coordination/runs/P1-B1-gate-lint-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-gate-mypy-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests` | no issues in 19 source files, exit 0 | `.local/coordination/runs/P1-B1-gate-mypy-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-gate-scanner-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` | PASS; 31 filesystem files, 31 index files, 0 issues, exit 0 | `.local/coordination/runs/P1-B1-gate-scanner-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-gate-build-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build` | sdist and wheel built, exit 0 | `.local/coordination/runs/P1-B1-gate-build-01/{metadata.json,stdout.bin,stderr.bin}` |

The scanner reported `git_tracking.available=true`, `excluded_tracked_files=0`, and no diagnostics. Its excluded paths were `.git`, `.local`, caches, `dist`, and `febio_cae.egg-info`.

## Initial candidate wheel and installed smoke (superseded by R1)

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: dda9f35c1ea4fdeaa442df1e3b6ad355233bffa1a6184001f4591f610339ffdd
Size: 21126 bytes
```

The fresh installation root was `.local/verification/P1-B1-installed-01`; the venv was created at its `venv` child and the wheel was installed non-editably with no index access.

| Record | Result | Raw evidence |
|---|---|---|
| `P1-B1-installed-preflight-01` | fresh install root absent, exit 0 | `.local/coordination/runs/P1-B1-installed-preflight-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-installed-wheel-hash-01` | SHA/size above, exit 0 | `.local/coordination/runs/P1-B1-installed-wheel-hash-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-installed-venv-01` | fresh Python 3.12 venv, exit 0 | `.local/coordination/runs/P1-B1-installed-venv-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-installed-pip-01` | `Successfully installed febio-cae-0.1.0`, exit 0 | `.local/coordination/runs/P1-B1-installed-pip-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-installed-cli-01` | `febio-cae 0.1.0`, exit 0 | `.local/coordination/runs/P1-B1-installed-cli-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-installed-import-01` | Python 3.12.10, isolated=1, `PYTHONPATH=None`, `PYTHONHOME=None`; package and domain imported from venv site-packages; metadata/module version `0.1.0`, exit 0 | `.local/coordination/runs/P1-B1-installed-import-01/{metadata.json,stdout.bin,stderr.bin}` |

This smoke verifies package distribution and import boundaries only. It does not establish solver, FBS, Studio, input-generation, or real-model success.

## R1 semantic remediation

The independent review of initial production SHA `142442583394926e101d9915c462d8787ec8cba9` reproduced 8 failures among 24 probes (16 passed, exit 1). The captured probe source and output are retained at `C:\Users\backo\.codex\worktrees\2e7d\CAE-harness\.local\review-p1b1-01\test_review.py`, `probe-run.json`, and `probe.stdout.txt`. The findings were limited to four semantic gaps:

- `SelectionRef.to_bytes()` did not canonicalize the semantic face sets. R1 passes only the present `rule.face_ids` and `resolution.faces` paths as explicit `unordered_paths` to the existing canonical serializer; ordered predicates, matrices, and scalar components remain ordered.
- An explicit `FaceSetRule` accepted a resolution with missing or extra faces. R1 requires equality of the supplied face-identity sets, independent of member order, while doing no CAD resolution work.
- Any syntactically valid `EvidenceRef` could be used for `role_evidence`. R1 accepts the explicit `selection.role` target convention only; this structural check does not elevate an evidence reference into physical authority.
- Direct norm computation underflowed subnormal direction components. R1 scales by the maximum absolute component before computing the norm, preserving finite signs and non-World frame behavior.

R1 regression tests were committed before production changes in `5d2bd6cc3d22db2e583ffc4757ce66ab4b296e32`. A fixture-only test correction (`0d85ec64b77c85c1747cd8a947f137ab8cbd8f20`) fixed non-hex seed arguments before the accepted RED run. The first `P1-B1-r1-red-01` run is retained as non-evidence because those invalid fixtures caused setup `ValueError`s; it is not counted below.

At clean test-only SHA `0d85ec64b77c85c1747cd8a947f137ab8cbd8f20`, `P1-B1-r1-red-basetemp-preflight-02` verified a fresh basetemp (exit 0). The exact command was:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_spatial.py tests/unit/contracts/test_selection.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B1-r1-red-02
```

`P1-B1-r1-red-02` collected 30 tests, with 8 behavioral failures and 22 passes, exit 1. The failures were the four reviewed semantic gaps (three direction cases, two canonical-order cases, two face-set mismatch cases, and one role-target case); there were no collection, import-setup, `KeyError`, or `AttributeError` failures. Raw evidence is `.local/coordination/runs/P1-B1-r1-red-02/{metadata.json,stdout.bin,stderr.bin}`.

R1 production SHA `a401c035b51634914b161acdfd9cf83b7b13dd0c` passed the fresh focused command as `P1-B1-r1-green-01`: 30 passed, exit 0, with `P1-B1-r1-green-basetemp-preflight-01` exit 0. Raw evidence is `.local/coordination/runs/P1-B1-r1-green-01/{metadata.json,stdout.bin,stderr.bin}`. The reviewer probe was replayed separately—not merged into the full-test count—with:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -X utf8 -m pytest C:\Users\backo\.codex\worktrees\2e7d\CAE-harness\.local\review-p1b1-01\test_review.py -c C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\pyproject.toml --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B1-r1-review-probe-01
```

`P1-B1-r1-review-probe-01` collected 24 and passed all 24, exit 0. Its fresh-path preflight is `P1-B1-r1-review-probe-basetemp-preflight-01` (exit 0). The final R1 production commit changes only the two authorized source files.

## R1 final local gates

All R1 gates below ran at clean SHA `a401c035b51634914b161acdfd9cf83b7b13dd0c`, with dirty state empty before and after. The runner retained exact argv, cwd, interpreter, HEAD, UTC start/end, exit code, and raw stdout/stderr paths.

| Record | Exact command | Result | Raw evidence |
|---|---|---|---|
| `P1-B1-r1-gate-pytest-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B1-r1-gate-pytest-01` | 107 passed, exit 0 | `.local/coordination/runs/P1-B1-r1-gate-pytest-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-r1-gate-format-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .` | 30 files formatted, exit 0 | `.local/coordination/runs/P1-B1-r1-gate-format-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-r1-gate-lint-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .` | all checks passed, exit 0 | `.local/coordination/runs/P1-B1-r1-gate-lint-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-r1-gate-mypy-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests` | no issues in 19 source files, exit 0 | `.local/coordination/runs/P1-B1-r1-gate-mypy-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-r1-gate-scanner-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` | PASS; 32 filesystem files, 32 index files, 0 issues, exit 0 | `.local/coordination/runs/P1-B1-r1-gate-scanner-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-r1-gate-build-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build` | sdist and wheel built, exit 0 | `.local/coordination/runs/P1-B1-r1-gate-build-01/{metadata.json,stdout.bin,stderr.bin}` |

The R1 scanner reported `git_tracking.available=true`, `excluded_tracked_files=0`, `issues=[]`, and the expected exclusions `.git`, `.local`, caches, `dist`, and `febio_cae.egg-info`.

## R1 wheel and installed smoke

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 7b7d1069e1dfaa8722594127ce5d7fc92ccbf86841dfa0b6197dc822afe3def7
Size: 21407 bytes
```

The fresh R1 installation root was `.local/verification/P1-B1-r1-installed-01`; the wheel was installed with `--no-index --no-deps --force-reinstall` into a new Python 3.12 venv.

| Record | Result | Raw evidence |
|---|---|---|
| `P1-B1-r1-installed-preflight-01` | fresh install root absent, exit 0 | `.local/coordination/runs/P1-B1-r1-installed-preflight-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-r1-installed-wheel-hash-01` | SHA/size above, exit 0 | `.local/coordination/runs/P1-B1-r1-installed-wheel-hash-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-r1-installed-venv-01` | fresh Python 3.12 venv, exit 0 | `.local/coordination/runs/P1-B1-r1-installed-venv-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-r1-installed-pip-01` | `Successfully installed febio-cae-0.1.0`, exit 0 | `.local/coordination/runs/P1-B1-r1-installed-pip-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-r1-installed-cli-01` | `febio-cae 0.1.0`, exit 0 | `.local/coordination/runs/P1-B1-r1-installed-cli-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-r1-installed-import-01` | Python 3.12.10, isolated=1, `PYTHONPATH=None`, `PYTHONHOME=None`; `febio_cae`, `domain`, `spatial`, and `selection` all imported from venv site-packages; metadata/module version `0.1.0`, exit 0 | `.local/coordination/runs/P1-B1-r1-installed-import-01/{metadata.json,stdout.bin,stderr.bin}` |

R1 installed smoke remains package/import evidence only; it does not establish solver, FBS, Studio, input-generation, or real-model success.

## Unverified items and next task

- Remaining P1 lifecycle work—case registration, issued-question generation, revision/state transitions, atomic persistence, and conflict/CAS behavior—is not implemented or verified here.
- Real FEBio, official FBS, FEBio Studio, Gmsh, LLM/native, authorized `02_CAE`, and BottomFrame real-model E2E remain unperformed.
- The spatial and selection values are contract-level synthetic objects; their connection to real CAD topology, solver profiles, loads, constraints, contacts, ROIs, or model assets remains unverified.
- The reviewer’s 24-probe replay passes at the R1 candidate, but PM acceptance, integration into `V2`, and any push are outside this worker handoff.

Next task: PM should perform the independent exact-commit review, then integrate only the reviewed clean commit sequence into `V2`.

## Report-stage postchecks

The report-stage postchecks were captured before this docs-only correction, at HEAD `a401c035b51634914b161acdfd9cf83b7b13dd0c` with only this report staged (`M  docs/reviews/2026-09-07-p1-b1-spatial-selection.md`). Both records retained the expanded command, cwd `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness`, Python runner `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe`, timestamps, staged dirty state, and raw output paths:

| Record | Exact argv and captured result | Raw evidence |
|---|---|---|
| `P1-B1-r1-report-diff-check-01` | `git diff --cached --check`; started `2026-09-07T06:42:29.494624Z`, finished `2026-09-07T06:42:29.539624Z`, exit 0; HEAD before/after `a401c035b51634914b161acdfd9cf83b7b13dd0c`; staged dirty state before/after `M  docs/reviews/2026-09-07-p1-b1-spatial-selection.md` | `.local/coordination/runs/P1-B1-r1-report-diff-check-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B1-r1-report-scanner-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .`; started `2026-09-07T06:42:29.806786Z`, finished `2026-09-07T06:42:32.674675Z`, exit 0; PASS with 32 filesystem files, 32 index files, and 0 issues; HEAD before/after `a401c035b51634914b161acdfd9cf83b7b13dd0c`; staged dirty state before/after `M  docs/reviews/2026-09-07-p1-b1-spatial-selection.md` | `.local/coordination/runs/P1-B1-r1-report-scanner-01/{metadata.json,stdout.bin,stderr.bin}` |

These records are evidence for the report state that was staged at that time; they do not claim execution on the clean code SHA or on this later docs-only commit. Product RED/GREEN and code gates were not rerun for this documentation correction.
