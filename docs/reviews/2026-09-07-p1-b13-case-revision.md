# P1-B13 CaseRevision content contract

Date: 2026-09-08
Scope: FEBio CAE Harness V2 immutable `CaseRevision` declaration, content identity, and local package gates
Decision boundary: this report covers the local domain contract, static gates, package build, and installed-package smoke only.

This is synthetic/local evidence. It is not evidence of a native FEBio input vocabulary, native solver support, profile registration, trusted evidence resolution, solver execution, official FBS, FEBio Studio, real-model execution, `02_CAE`, or BottomFrame success.

## Fixed Git boundary

| Item | Value |
|---|---|
| Worker branch | `codex/p1-b13-case-revision` |
| Accepted integrated base | `edbc4c9e2bccec49f7db6467c155c131d32f4200` |
| Initial test-only contract SHA | `8e23597c64d489ec345171291256f5287559c4ae` |
| Test fixture correction SHA | `6b4a2cb309685b1b77710fd3da5e90a93ac69944` |
| Test helper correction SHA | `27c36f5b5721c95be4eddd1eb5e4c19df15fd110` |
| Production candidate | `00e172cc773f376fea227c8eeef30eedec508053` |
| Authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| Remote state | `REMOTE_CONFIGURED` |
| Integration/push | not performed by this worker |

The final product delta from the accepted base is exactly these five tracked paths; the report is separate from the four-path code/test change:

| Path | Change |
|---|---|
| `src/febio_cae/domain/case_revision.py` | frozen/slotted `CaseRevision`, content identity, and `CaseRevisionValidationError` |
| `src/febio_cae/domain/__init__.py` | public `CaseRevision` and `CaseRevisionValidationError` exports only |
| `tests/unit/contracts/test_case_revision.py` | synthetic contract tests |
| `tests/unit/contracts/conftest.py` | non-autouse, fully explicit synthetic `CaseSpec` fixture |
| `docs/reviews/2026-09-07-p1-b13-case-revision.md` | this review report |

No compiler, registration, persistence, CLI, native reader, solver, FBS, or real-model path was changed.

## Implemented contract

`CaseRevision` is a frozen, slotted value with six mandatory constructor inputs: opaque `case_id`, opaque `revision_id`, paired optional parent revision ID and lowercase SHA-256 parent specification digest, an existing `CaseSpec`, and a nonempty sequence of existing `EvidenceRef` declarations. Omitted Python arguments retain the standard generated-initializer `TypeError` behavior.

The constructor validates nonempty identifiers without surrounding whitespace or Unicode `Cc` control characters, requires UTF-8 representability, enforces the parent ID/digest pair and self-parent rejection, checks the exact child types, copies evidence into a tuple, rejects duplicate complete evidence declarations, and canonically orders the evidence set. It does not inspect parent records, infer ancestry, resolve sources, or manufacture physical values.

`content_bytes()` consumes the existing `CaseSpec.to_bytes()` projection and public `EvidenceRef.to_dict()` projections through the shared `canonical_bytes` owner. Its exact schema-1 content is `schema_version`, the complete nested `spec`, and the explicit evidence declaration set; evidence order is semantically irrelevant. `spec_digest` is the lowercase SHA-256 of those bytes and excludes case/revision IDs, parent linkage, timestamps, and the digest itself. `to_dict()` and `to_bytes()` add the record identity fields, paired parent fields, spec, evidence, and computed digest as a detached complete-record projection.

The value is content identity only. It does not freeze a draft, certify registered provenance or evidence freshness, set `READY`, grant execution permission, or provide a registration/freeze service.

## Test-first chronology and preserved failures

Every wrapper record preserves expanded argv, actual cwd, UTC timestamps, HEAD, dirty state, exit code, and raw stdout/stderr under `.local/coordination/runs/<record-id>/`. Failed attempts remain nonpassing evidence and are not relabeled as later passes.

The initial test-only commit was followed by two narrowly scoped test corrections:

- `6b4a2cb` corrected five fixture role-evidence targets from child-specific names to the existing public `selection.role` target. The first attempted RED, `P1-B13-red-01`, is therefore invalid setup evidence: it collected 74 items but ended with `1 failed, 73 errors` because the fixture failed before the intended API boundary.
- `27c36f5` corrected two test helper parameter-name collisions that prevented the wrong-spec cases from reaching the constructor. It did not change the contract assertions.

The genuine availability RED is anchored to clean `6b4a2cb309685b1b77710fd3da5e90a93ac69944` after the fixture correction. The later `27c36f5b5721c95be4eddd1eb5e4c19df15fd110` helper repair happened after the initial production implementation had begun; it is a test-helper correction and does not retroactively become preimplementation RED evidence.

| Record | Exact command/result | Exit | Attribution |
|---|---|---:|---|
| `P1-B13-red-preflight-01` | fresh `.../.local/verification/P1-B13-red-01` absent | 0 | preflight only |
| `P1-B13-red-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/unit/contracts/test_case_revision.py --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B13-red-01`; 74 collected, `1 failed, 73 errors` | 1 | invalid RED: fixture setup error in role-evidence targets |
| `P1-B13-red-preflight-02` | fresh `.../.local/verification/P1-B13-red-02` absent | 0 | preflight only |
| `P1-B13-red-02` | same focused command with `.../P1-B13-red-02` at clean `6b4a2cb`; `1 failed, 73 skipped` in 0.38s; only the intended API-availability assertion failed | 1 | valid availability RED; no collection/setup/environment failure |
| `P1-B13-green-01` | focused command at dirty production source after the initial implementation; `6 failed, 68 passed` | 1 | preserved: five helper collisions plus frozen `spec_digest` assignment behavior |
| `P1-B13-green-02` | focused command after the first helper correction; `5 failed, 69 passed` | 1 | preserved: remaining `_revision_kwargs` helper collision |
| `P1-B13-green-03` | focused command at test-only SHA `27c36f5`, with source changes dirty; `74 passed` in 0.35s | 0 | focused GREEN |

The `green-01` and `green-02` nonpassing records remain preserved exactly as above: they are post-initial-implementation correction evidence, not preimplementation RED evidence.

The initial test-only static setup also remains recorded separately. `P1-B13-test-lint-01` exited 1 on the import-order diagnostic; `P1-B13-test-lint-fix-01` exited 0 after the formatter/import-order recovery, and `P1-B13-test-lint-02` exited 0. `P1-B13-test-format-fix-01` reformatted the two new test files and `P1-B13-test-format-02` then exited 0. These are setup/static corrections, not product-test failures.

The focused contract covers mandatory omissions, identifier and Unicode boundaries, parent pairing/self-parent/digest shape, wrong spec/evidence types, duplicate and order-independent evidence, canonical content/digest identity, changed nested content, SI/child semantic-set equivalence, record-versus-content identity, exact hashlib agreement, self-hash exclusion, frozen storage, detached caller/projection behavior, and equal parent/current content digests.

## Final clean gates

All final product gates below ran from clean production SHA `00e172cc773f376fea227c8eeef30eedec508053`; the full test used a fresh basetemp.

| Record | Exact command/result | Exit |
|---|---|---:|
| `P1-B13-gate-pytest-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B13-gate-pytest-01`; `900 passed` in 18.40s | 0 |
| `P1-B13-gate-format-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff format --check .`; 77 files already formatted | 0 |
| `P1-B13-gate-lint-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff check .`; all checks passed | 0 |
| `P1-B13-gate-mypy-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m mypy src tests`; no issues in 48 source files | 0 |
| `P1-B13-gate-scanner-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe scripts/scan_cae_data.py --root .`; PASS, 79 tracked/index files checked, 0 diagnostics | 0 |
| `P1-B13-gate-build-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m build`; sdist and wheel built | 0 |

The scanner excluded only standard local/build paths and reported `status=PASS`, `diagnostics=[]`, and `issues=[]`.

## Wheel and outside-checkout installed smoke

The exact wheel built by `P1-B13-gate-build-01` is:

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 705e921072f4a230a8b4032f2512ff184f3066b1bb87301c297b7f7d2d662055
Size: 55863 bytes
```

`P1-B13-installed-preflight-01` verified that `C:\Users\backo\AppData\Local\Temp\cae-harness-P1-B13-installed-01` was absent. A fresh Python 3.12 virtual environment was created outside the checkout and the exact wheel was installed offline.

| Record | Result |
|---|---|
| `P1-B13-wheel-hash-01` | exact wheel size and SHA-256 above; exit 0 |
| `P1-B13-installed-preflight-01` | fresh external root absent; exit 0 |
| `P1-B13-installed-venv-01` | fresh Python 3.12 venv; exit 0 |
| `P1-B13-installed-pip-01` | exact wheel installed with `--no-index --no-deps --force-reinstall`; exit 0 |
| `P1-B13-installed-cli-01` | `febio-cae 0.1.0`; exit 0 |
| `P1-B13-installed-import-01` | isolated `-I`; package and `case_revision` origins under the external venv and outside checkout; both CaseRevision aliases true; fully explicit synthetic CaseRevision construction and SHA-256 digest agreement succeeded; `spec_digest=edaccf936e68eed57336075d5d93c180e4f4439082d7705f9555ddfffee8d145`, complete record 19,915 bytes; exit 0 |

The installed smoke proves packaging, import provenance, public export identity, and one synthetic canonical CaseRevision construction only. It does not establish profile registration, native variable support, solver launch, result-reader compatibility, physical unit or ROI correctness, result completeness, quality assessment, official FBS, FEBio Studio, or real-model success.

## Report-only checks

After the original report text was complete, only the report was staged at dirty HEAD `00e172cc773f376fea227c8eeef30eedec508053`. The report-only checks passed separately from the clean production scanner:

| Record | Result | Exit |
|---|---|---:|
| `P1-B13-report-diff-check-01` | staged `git diff --cached --check`; report was the only staged path | 0 |
| `P1-B13-report-scanner-01` | staged-report scan PASS, 80 indexed/tracked files, 0 diagnostics | 0 |

The clean production `P1-B13-gate-scanner-01` remains distinct at 79 indexed/tracked files and zero diagnostics.

For this report-only attribution correction, the final-text checks ran with cwd `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness`, HEAD `c1da1743407a3a7412b3843b982c819c010c7e43`, and `dirty_before`/`dirty_after` exactly `M  docs/reviews/2026-09-07-p1-b13-case-revision.md` (only this report staged):

| Record | Result | Exit |
|---|---|---:|
| `P1-B13-R1-report-diff-check-02` | staged `git diff --cached --check`; report was the only staged path | 0 |
| `P1-B13-R1-report-scanner-02` | staged-report scan PASS, 80 indexed/tracked files, 0 diagnostics | 0 |

## Unverified items and handoff boundary

- Revision registration, parent existence/case membership, draft-generation freshness, evidence lookup and freshness, capability validation, freezing, READY recomputation, compilation, solver execution, and result receipts remain later services or unverified.
- No real FEBio/FBS/FEBio Studio run, native model/result read, authorized `02_CAE` data access, BottomFrame run, or real-model E2E was performed.
- The focused and full tests are synthetic contract tests. Passing results do not establish physical correctness, material/load/contact meaning, numerical compatibility, or execution readiness.
- No schema freeze, integration, push, or product-completion claim is made by this slice.

Next sequence: independent exact whole review of the final clean report-bearing descendant of production candidate `00e172cc773f376fea227c8eeef30eedec508053`, including all five tracked paths; after whole-review acceptance, PM-only integration into `V2` followed by fresh post-integration gates. This worker will not integrate, push, access native helpers, access real `02_CAE` data, or access BottomFrame.
