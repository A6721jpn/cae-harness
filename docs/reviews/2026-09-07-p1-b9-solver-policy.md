# P1-B9 solver policy and numerical intent

Date: 2026-09-07
Scope: FEBio CAE Harness V2 immutable solver numerical intent and time-subdivision data
Decision boundary: this report covers local domain tests, static gates, package build, and installed-package smoke only.

This is synthetic/local evidence. It is not evidence of a native FEBio control vocabulary, residual physical units, native profile support, registry trust, solver execution, retry execution, CaseSpec readiness, official FBS, FEBio Studio, real-model execution, `02_CAE`, or BottomFrame success. A `NumericalProfileRef` identifies a supplied record only; it does not prove registration, freshness, compatibility, native support, or execution permission.

## Fixed Git boundary

| Item | Value |
|---|---|
| Working branch | `codex/p1-b9-solver-policy` |
| Accepted integrated P1-B8 base | `4688275b236c5bc1ddc5978b85187b8337ea8b3b` |
| P1-B9 test-only contract SHA | `0c267e904e45ac69ddf0b1a69851781440bab2cf` |
| first P1-B9 production SHA | `978da8116f060ecf6a85f0c2409963d79bee1a5b` |
| P1-B9 production gate-correction SHA | `23efd9823d00c86805772d8cfbd81edba23ff6a4` |
| final production candidate | `23efd9823d00c86805772d8cfbd81edba23ff6a4` |
| authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote state | `REMOTE_CONFIGURED` |
| push/integration | not performed by this worker |

The tracked implementation changes are limited to `src/febio_cae/domain/solver_policy.py`, four exports in `src/febio_cae/domain/__init__.py`, `tests/unit/contracts/test_solver_policy.py`, and this report. Existing `NumericalProfileRef`, `MotionProfile`, `Budget`, mesh, material, support, contact, geometry, registry, persistence, CaseSpec, and CLI code were not changed.

## Authority and native boundary

The design authority requires `solver_policy` to retain a compatible solver profile, numerical tolerances, increment control, and retry recipes without changing physical material, support, friction, final displacement, or load history. Time subdivision is data over the existing explicit `MotionProfile` timeline, not a second motion history and not a retiming mechanism. Numerical convergence values are separate from physical quality criteria; no residual dimension is inferred from a parameter name.

The planning note records only documentary FEBio references for convergence norms and time-controller retry/must-point concepts. It explicitly limits this slice from hardcoding native vocabulary/defaults or claiming support from documentation alone. The implementation therefore uses synthetic control and recipe identifiers, accepts no XML, executable, arbitrary path, or code payload, and performs no native mapping or profile resolution.

## Implemented contract

- `SolverControl` is immutable and requires a strict ASCII identifier beginning with a letter or underscore, followed by ASCII letters, digits, or underscores. Its value is exactly a `Quantity`, integer excluding `bool`, or `bool`; raw floats, strings, containers, and arbitrary objects are rejected. Signed values and explicit zero remain structural data. Canonical projection tags `quantity`, `integer`, or `boolean`, preserving distinctions such as integer `1`, `True`, and `Quantity(1, "1")`; quantities use shared SI projection.
- `TimeIncrementPolicy` requires explicit positive time quantities for `initial_step`, `minimum_step`, and `maximum_step`, strict `bool` `adaptive`, strict integer `max_steps >= 1`, strict integer `max_step_retries >= 0`, and an explicit possibly-empty immutable `must_points` tuple. It enforces minimum <= initial <= maximum in SI, nonnegative strictly increasing must-points with no duplicates, and equivalent SI canonical identity. It does not create a start/final time, default, or replacement physical history.
- `SolverPolicy` requires an existing `NumericalProfileRef` with `purpose="solver"`, a nonempty immutable semantic control set sorted by control name with duplicate names rejected, a `TimeIncrementPolicy`, and an explicit possibly-empty ordered immutable retry-recipe tuple. Retry IDs use the same identifier grammar, preserve order and repetition, and carry no execution permission.
- All three constructors force complete canonical projection and wrap nested canonical/SI failures with field context. Large integers within the shared JSON contract remain exact; values beyond that boundary are rejected at construction. No resolver, registry, native capability, retry executor, Budget accounting, CaseSpec aggregation, or readiness checker is introduced.

## Test-first RED / GREEN chronology

The exact wrapper records below retain expanded argv, cwd, Python runner, UTC timestamps, HEAD, dirty state, exit code, and raw stdout/stderr paths under `.local/coordination/runs/`.

### Test-only draft and availability RED

Before the test commit, the draft was formatted and linted:

| Record | Exact command | Result |
|---|---|---|
| `P1-B9-test-draft-format-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff format --check tests/unit/contracts/test_solver_policy.py` | 1 file already formatted, exit 0 |
| `P1-B9-test-draft-lint-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff check tests/unit/contracts/test_solver_policy.py` | all checks passed, exit 0 |

The clean test-only commit was `0c267e904e45ac69ddf0b1a69851781440bab2cf`. `P1-B9-red-01` ran:

```text
C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/unit/contracts/test_solver_policy.py --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B9-red-01
```

It collected 91 tests and exited `1` with one intentional API-availability failure and 90 skips. There were no collection, setup, or environment failures. This is availability RED, not semantic behavioral evidence.

### Production GREEN and gate correction

Production commit `978da8116f060ecf6a85f0c2409963d79bee1a5b` added the module and package exports. The dirty precommit focused run `P1-B9-green-precommit-01` passed 91 tests in 0.10 seconds, exit `0`. The clean focused run `P1-B9-green-01` passed 91 tests in 0.10 seconds, exit `0`.

The first clean full-gate run preserved two failures. `P1-B9-gate-lint-01` exited `1` on Ruff `RUF022` because the package `__all__` ordering placed `TimeIncrementPolicy` after `Translation3`. `P1-B9-gate-mypy-01` exited `1` on one `object`-typed control item lacking a visible `name` attribute. These were implementation-gate corrections, not test or environment failures. The first full pytest (603 passed), format (64 files), scanner (66 files, 0 diagnostics), and build all exited `0` and remain preserved.

The smallest correction commit `23efd9823d00c86805772d8cfbd81edba23ff6a4` only fixes the export order and adds the runtime-validation narrowing cast. Correction checks `P1-B9-fix-format-01`, `P1-B9-fix-lint-01`, and `P1-B9-fix-mypy-01` all exited `0`. The fresh clean focused GREEN `P1-B9-green-02` then passed 91 tests in 0.11 seconds, exit `0`.

## Final local gates

All final records below ran at clean production candidate `23efd9823d00c86805772d8cfbd81edba23ff6a4`, with empty `dirty_before` and `dirty_after`.

| Record | Exact command | Result |
|---|---|---|
| `P1-B9-green-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/unit/contracts/test_solver_policy.py --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B9-green-02` | 91 passed in 0.11s, exit 0 |
| `P1-B9-gate-pytest-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B9-gate-pytest-02` | 603 passed in 15.86s, exit 0 |
| `P1-B9-gate-format-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff format --check .` | 64 files already formatted, exit 0 |
| `P1-B9-gate-lint-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff check .` | all checks passed, exit 0 |
| `P1-B9-gate-mypy-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m mypy src tests` | no issues in 39 source files, exit 0 |
| `P1-B9-gate-scanner-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe scripts/scan_cae_data.py --root .` | PASS; 66 files checked, 0 diagnostics, exit 0 |
| `P1-B9-gate-build-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m build` | sdist and wheel built, exit 0 |

The earlier `P1-B9-gate-*-01` records remain preserved, including the two failed correction gates. Only the corrected `-02` suite/gates are final acceptance evidence. The suite is synthetic/local unit and component evidence only.

## Wheel and outside-checkout installed smoke

The wheel built at final production candidate `23efd9823d00c86805772d8cfbd81edba23ff6a4` was:

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 8296d1f019d554fd876d880b547d764d65a4b8c503a755b86e3453ed60b0f6ab
Size: 45343 bytes
```

A fresh Python 3.12 venv was created outside the checkout at `C:\Users\backo\AppData\Local\Temp\cae-harness-P1-B9-installed-01`. All records below exited `0`; outside-cwd records used the actual checkout as `git-workdir`.

| Record | Result |
|---|---|
| `P1-B9-installed-wheel-hash-01` | SHA-256 and 45,343-byte size above |
| `P1-B9-installed-outside-preflight-01` | fresh outside root absent |
| `P1-B9-installed-outside-venv-01` | new Python 3.12 venv |
| `P1-B9-installed-outside-pip-01` | exact wheel installed offline with `--no-index --disable-pip-version-check --no-cache-dir --no-deps` |
| `P1-B9-installed-outside-cli-01` | `febio-cae 0.1.0` |
| `P1-B9-installed-outside-import-01` | isolated `-I`; version `0.1.0`; identities for all four new package/module exports; synthetic solver policy projection; all module origins under the external venv and outside the checkout |

Installed smoke is package/import evidence only. It does not establish profile registration, native control support, solver launch permission, retry execution, or real-model success.

## Report-stage postchecks

After the final report text was complete, only this report was staged. The staged checks retain expanded argv, cwd, Python runner, UTC timestamps, staged dirty state, raw output paths, and production candidate `23efd9823d00c86805772d8cfbd81edba23ff6a4` in their metadata records.

| Record | Result |
|---|---|
| `P1-B9-report-diff-check-final-01` | direct `git diff --cached --check`; exit 0 |
| `P1-B9-report-scanner-final-01` | `scripts/scan_cae_data.py --root .`; PASS with 67 files checked, 0 diagnostics, exit 0 |

## Unverified items and remaining sequence

- Immutable profile records/catalog, registered control schemas, allowed value kinds/units/ranges, exact native version/dependencies, freshness, resolver compatibility, validation receipts, and native capability remain unimplemented or unverified.
- No residual norm or convergence parameter was assigned a physical unit from its name. No native control vocabulary, default, XML mapping, executable, retry recipe execution, or forced-convergence success path is implemented.
- Time-increment data has not been checked against a `MotionProfile`'s start/end/breakpoints or required output states by a CaseSpec/compiler. No physical displacement history, material, support, friction, load history, geometry, or mesh data can be changed by this slice.
- Budget accounting, new AttemptRecords/output areas, lifecycle/CAS, atomic persistence, artifact contracts, official FBS, FEBio Studio, native profiles, real CAD/mesh inputs, authorized `02_CAE` data, BottomFrame, and all real-model E2E remain unperformed.
- No schema freeze, native readiness, execution authority, authorized execution, or product completion claim is made by this slice.

The solver-policy correction and evidence handoff are complete through the candidate and report checks above. Remaining sequence: an independent exact whole review of this clean candidate; after an exact whole `ACCEPT`, PM-only integration into `V2` followed by fresh post-integration gates. This worker will not push, integrate, access native helpers, access real `02_CAE` data, or access BottomFrame.
