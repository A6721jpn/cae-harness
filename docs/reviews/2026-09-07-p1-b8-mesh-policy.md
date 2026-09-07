# P1-B8 mesh policy and numerical profile reference

Date: 2026-09-07
Scope: FEBio CAE Harness V2 immutable mesh sizing and profile-reference intent
Decision boundary: this report covers local domain tests, static gates, package build, and installed-package smoke only.

This is synthetic/local evidence. It is not evidence of a mesh generator, real CAD or mesh inspection, official FBS, FEBio Studio, native profile support, registry trust, numerical quality validation, CaseSpec readiness, real-model execution, `02_CAE`, or BottomFrame success. A profile reference identifies a supplied record only; it does not prove registration, freshness, native support, or execution permission.

## Fixed Git boundary

| Item | Value |
|---|---|
| Working branch | `codex/p1-b8-mesh-policy` |
| Accepted integrated V2 base | `0137ec38034645cd75b98e8468e153017b44e3fb` |
| test-only contract SHA | `8c055833267e1e1bef6dd5a7867985cacaee913b` |
| test-only lint correction SHA | `c4567c5ed973e45948e67fd21bfdce08ad141c9f` |
| test-only canonical fixture correction SHA | `e621e9fec3f02376d8db9aa423ea48ce783519a9` |
| first production SHA | `d34d39f92bbf35e621bd0b783a6e1f2284ef5c11` |
| test-only typing correction SHA | `17ed4d565245e48e326c3ef6f415618b1f967c46` |
| final production SHA | `c611141d4b601034f36bf8db5ec41002ac5908cf` |
| authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote state | `REMOTE_CONFIGURED` |
| push/integration | not performed by this worker |

The tracked implementation changes are limited to the four authorized paths: new `src/febio_cae/domain/mesh_policy.py`, exports in `src/febio_cae/domain/__init__.py`, new `tests/unit/contracts/test_mesh_policy.py`, and this report. Existing quantities, units, spatial transforms, evidence, selection, rigid, support, contact, material, motion, geometry, budget, solver/profile adapters, registry, persistence, CaseSpec, and CLI code were not changed.

## Implemented contract

- `NumericalProfileRef` is an immutable identity value with required `profile_id`, `purpose`, and lowercase `record_digest`. Purpose is explicitly one of `mesh_quality`, `solver`, `outputs`, or `quality`; only `mesh_quality` is accepted by `MeshPolicy`. The reference has no registry, numeric-criteria, freshness, executable, native-capability, or caller-approved semantics.
- `LocalRefinement` is an immutable named request with required `refinement_id`, existing `SelectionRef`, and strictly positive length `size`. Its size is an upper bound and is compared in shared SI units against the policy global size. Existing selection role evidence and face-set canonical semantics remain authoritative; no physical meaning is inferred from a selection.
- `MeshPolicy` requires explicit `element_type="tet10"`, positive length `global_size`, a required possibly-empty local-refinement collection, a mesh-quality `NumericalProfileRef`, and nonnegative strict-integer `max_refinements`. Missing fields do not default. Local requests are copied to an immutable tuple, duplicate IDs are rejected, and canonical projection sorts the semantic set by identifier.
- Equivalent SI global/local sizes have identical canonical bytes. Face-set selection permutations retain the existing semantic-set identity through the nested parent projection. Construction validates complete nested canonical totality, including unrepresentable SI values, invalid nested text, serializable large integers, and rejection of integers beyond the shared JSON digit boundary.
- No mesh generation, profile resolution, numeric quality threshold, native mesher selection, physical support/contact mutation, node merge, registration, readiness, or CaseSpec cross-object validation is introduced.

## Test-first RED / GREEN chronology

The exact wrapper records below retain expanded argv, cwd, Python runner, UTC timestamps, HEAD, dirty state, exit code, and raw stdout/stderr paths under `.local/coordination/runs/`.

### Availability RED

At clean test-only correction SHA `c4567c5ed973e45948e67fd21bfdce08ad141c9f`, `P1-B8-red-01` ran:

```text
C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/unit/contracts/test_mesh_policy.py --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B8-red-01
```

It collected 93 tests and exited `1` with one intentional API-availability failure and 92 skips. There were no collection, setup, or environment failures. This is availability RED, not semantic behavioral evidence.

Before that RED, the initial test draft's focused Ruff check reported two test-only issues (`F401` unused `re` and `F841` unused helper assignment); `c4567c5` corrected them before the RED run. After RED, `e621e9f` corrected a skipped-only whole-body canonical fixture that declared a face-set path; it did not change production behavior.

### Focused GREEN and intermediate gate correction

Production commit `d34d39f92bbf35e621bd0b783a6e1f2284ef5c11` added the module and exports. `P1-B8-green-01` collected and passed all 93 tests in 0.12 seconds, exit `0`, with empty dirty state before and after.

The first full gate pass preserved `P1-B8-gate-mypy-01` as exit `1`: mypy reported one redundant production cast and one test-helper argument annotation that was too narrow for an intentional invalid-object case. No test, collection, scanner, or environment failure was involved. Test-only correction `17ed4d5` widened the invalid fixture annotation; production correction `c611141` removed the redundant cast. The corrected clean focused run `P1-B8-green-02` then collected and passed all 93 tests in 0.11 seconds, exit `0`, with empty dirty state before and after.

## Final local gates

All final records below ran at clean production SHA `c611141d4b601034f36bf8db5ec41002ac5908cf`; each has empty `dirty_before` and `dirty_after`.

| Record | Exact command | Result |
|---|---|---|
| `P1-B8-gate-pytest-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B8-gate-pytest-02` | 508 passed in 16.10s, exit 0 |
| `P1-B8-gate-format-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff format --check .` | 61 files already formatted, exit 0 |
| `P1-B8-gate-lint-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff check .` | all checks passed, exit 0 |
| `P1-B8-gate-mypy-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m mypy src tests` | no issues in 37 source files, exit 0 |
| `P1-B8-gate-scanner-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe scripts/scan_cae_data.py --root .` | PASS; 63 files checked, 0 diagnostics, exit 0 |
| `P1-B8-gate-build-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m build` | sdist and wheel built, exit 0 |

The earlier `P1-B8-gate-pytest-01`, format-01, lint-01, and mypy-01 records remain preserved; only the corrected `-02` suite/gates are final acceptance evidence. The final suite is synthetic/local unit and component evidence only.

## Wheel and outside-checkout installed smoke

The wheel built at final production SHA `c611141d4b601034f36bf8db5ec41002ac5908cf` was:

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 0ddcdae8244fa6da1548e7c99ec21653d57c0ea61c971ba2bfbf46b5e1b088ae
Size: 42842 bytes
```

A fresh Python 3.12 venv was created outside the checkout at `C:\Users\backo\AppData\Local\Temp\cae-harness-P1-B8-installed-01`. All records below exited `0`; outside-cwd records used the actual checkout as `git-workdir`.

| Record | Result |
|---|---|
| `P1-B8-installed-wheel-hash-01` | SHA-256 and 42,842-byte size above |
| `P1-B8-installed-outside-preflight-01` | fresh outside root absent |
| `P1-B8-installed-outside-venv-01` | new Python 3.12 venv |
| `P1-B8-installed-outside-pip-01` | exact wheel installed offline with `--no-index --disable-pip-version-check --no-cache-dir --no-deps` |
| `P1-B8-installed-outside-cli-01` | `febio-cae 0.1.0` |
| `P1-B8-installed-outside-import-01` | isolated `-I`; version `0.1.0`; identities for all four new package/module exports; explicit tet10 empty-local-refinement projection; all module origins under the external venv and outside the checkout |

Installed smoke is package/import evidence only. It does not establish profile registration, native profile support, mesh generation, solver launch permission, or real-model success.

## Report-stage postchecks

After the final report text was complete, only this report was staged. The staged checks retain expanded argv, cwd, Python runner, UTC timestamps, staged dirty state, raw output paths, and final production SHA `c611141d4b601034f36bf8db5ec41002ac5908cf` in their metadata records.

| Record | Result |
|---|---|
| `P1-B8-report-diff-check-final-01` | direct `git diff --cached --check`; exit 0 |
| `P1-B8-report-scanner-final-01` | `scripts/scan_cae_data.py --root .`; PASS with 64 files checked, 0 diagnostics, exit 0 |

## Unverified items and next task

- Numerical profile records, registry resolution, immutable profile criteria, digest association, freshness, native support, and actual mesh-quality validation remain unimplemented or unverified.
- Real selection existence/nonemptiness, geometry/body/frame association, overlap resolution, mesher behavior, mesh independence, solver mapping, output quality, and CaseSpec aggregation remain later responsibilities.
- Lifecycle/CAS, atomic persistence, artifact contracts, official FBS, FEBio Studio, native profiles, real CAD/mesh inputs, authorized `02_CAE` data, BottomFrame, and all real-model E2E remain unperformed.
- No schema freeze, native readiness, execution authority, authorized execution, or product completion claim is made by this slice.

Next task: stage only this report, run direct staged diff and final boundary scanner checks after the final text, commit a clean small report handoff, and send the exact full SHA to the PM. Independent whole review and PM-only integration remain required. This worker will not push or integrate.
