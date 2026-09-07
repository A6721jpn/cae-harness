# P1-B1 spatial and selection contract verification

Date: 2026-09-07
Scope: FEBio CAE Harness V2 P1-B1 spatial values and provenance-aware selection contracts
Decision boundary: this report covers the new domain contracts, local tests, static gates, package build, and installed-package smoke only.

This is synthetic/local evidence. It is not evidence of a real FEBio solve, official FBS, FEBio Studio, Gmsh, LLM, native-app, real-model, `02_CAE`, or BottomFrame success. No physical meaning is inferred from geometry or naming conventions; unresolved physical conditions remain outside this contract.

## Fixed Git boundary

| Item | Value |
|---|---|
| Working branch | `codex/p1-b1-spatial-selection` |
| P1-B1 base | `76176add3d270d7ca003f7bd09a61471f199beb5` |
| test-only API SHA | `922749aab84eb5244d47b0443317ed3c882c7ced` |
| test-only correction SHA | `40c1c4f303b70b3dcbddf70078cd794c9f418e13` |
| production SHA | `142442583394926e101d9915c462d8787ec8cba9` |
| final code candidate | `142442583394926e101d9915c462d8787ec8cba9` |
| authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote state | `REMOTE_CONFIGURED` |
| locally observed `origin/V2` | `f903575283136ffe45ff0c703bc3f6fab3462285` |
| push/integration | not performed by this worker |

The test-only commits add or tighten only the P1-B1 contract tests. The production commit adds only `domain/spatial.py`, `domain/selection.py`, and the corresponding `domain/__init__.py` exports. The clean candidate has no uncommitted tracked changes.

## Test-first RED / GREEN

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

## Final local gates

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

## Wheel and installed smoke

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

## Unverified items and next task

- Remaining P1 lifecycle work—case registration, issued-question generation, revision/state transitions, atomic persistence, and conflict/CAS behavior—is not implemented or verified here.
- Real FEBio, official FBS, FEBio Studio, Gmsh, LLM/native, authorized `02_CAE`, and BottomFrame real-model E2E remain unperformed.
- The spatial and selection values are contract-level synthetic objects; their connection to real CAD topology, solver profiles, loads, constraints, contacts, ROIs, or model assets remains unverified.
- Independent review of this final candidate, PM integration into `V2`, and any push are outside this worker handoff.

Next task: PM should perform the independent exact-commit review, then integrate only the reviewed clean commit sequence into `V2`.

## Report-stage postchecks

After staging this report, run `git diff --cached --check` and `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` through the capture runner. Both must exit 0; the scanner should report 32 filesystem/index files and 0 issues after this report is staged. These postchecks are report-integrity evidence and do not replace the final code gates above.
