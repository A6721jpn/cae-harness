# P1-B2 material and motion contract verification

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

Date: 2026-09-07
Scope: FEBio CAE Harness V2 P1-B2 bounded material and motion domain values
Decision boundary: this report covers immutable domain contracts, local tests, static gates, package build, and installed-package smoke only.

This is synthetic/local evidence. It is not evidence of a real FEBio solve, official FBS, FEBio Studio, Gmsh, LLM, native-app, real-model, `02_CAE`, or BottomFrame success. Candidate material vocabulary is not registered native authority or verified solver capability. No physical condition is inferred from geometry, defaults, or naming conventions.

## Fixed Git boundary

| Item | Value |
|---|---|
| Working branch | `codex/p1-b2-material-motion` |
| Integrated P1-B2 base | `7648c517ba047a676165b49b211d35e2ecffb6b6` |
| test-only contract SHA | `38061fb4818476eb77bcdb05fe7d6fede23812ea` |
| test-only correction SHA | `4d8eff283e1c442fc269791b072c51a8fec1069e` |
| production SHA | `6f07c680322d252003fae8e5da2d4d39adc81d29` |
| final code candidate | `6f07c680322d252003fae8e5da2d4d39adc81d29` |
| authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote state | `REMOTE_CONFIGURED` |
| locally observed `origin/V2` | `7648c517ba047a676165b49b211d35e2ecffb6b6` |
| push/integration | not performed by this worker |

The test-only commits add only the two new contract test files. The production commit adds only `domain/material.py`, `domain/motion.py`, and explicit exports in the existing `domain/__init__.py`. The code candidate is clean.

## Test-first RED / GREEN

All captured commands used `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe` (Python 3.12.10). The local runner records expanded argv, cwd, interpreter, HEAD and dirty state before/after, UTC start/end, exit code, and absolute stdout/stderr paths under `.local/coordination/runs/`.

### API-availability RED

At clean test-only SHA `38061fb4818476eb77bcdb05fe7d6fede23812ea`, `P1-B2-red-basetemp-preflight-01` verified the fresh basetemp was absent (exit 0). The exact command was:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_material.py tests/unit/contracts/test_motion.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B2-red-01
```

`P1-B2-red-01` collected 36 tests, with 2 API-availability assertion failures and 34 semantic tests skipped because the two entrypoints did not yet exist; exit 1. There were no collection, import-setup, or environment errors. This is availability RED only, not behavioral RED evidence. Raw evidence is `.local/coordination/runs/P1-B2-red-01/{metadata.json,stdout.bin,stderr.bin}`.

### Clean GREEN

At clean production SHA `6f07c680322d252003fae8e5da2d4d39adc81d29`, `P1-B2-green-basetemp-preflight-01` verified a fresh basetemp (exit 0). `P1-B2-green-01` ran:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_material.py tests/unit/contracts/test_motion.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B2-green-01
```

The result was 36 passed, exit 0. Raw evidence is `.local/coordination/runs/P1-B2-green-01/{metadata.json,stdout.bin,stderr.bin}`. The small test-only correction in `4d8eff2` moved the negative-displacement construction inside its expected exception boundary; no product behavior was changed by that correction.

## Implemented contracts

- `src/febio_cae/domain/material.py` provides immutable `IsotropicLinearElastic` and `CompressibleNeoHookean` candidates using one explicit E/nu parameterization. Young’s modulus requires a positive pressure `Quantity`; Poisson’s ratio requires a dimensionless `Quantity` satisfying strict `-1 < nu < 0.5`. No density, guessed strain limit, or guessed rate threshold is present.
- `MaterialApplicability` requires nonempty supplied strain and rate statements. Model identity, E, nu, strain applicability, and rate applicability each retain an `EvidenceRef` bound to the exact target fields `material.model`, `material.youngs_modulus`, `material.poisson_ratio`, `material.strain_applicability`, and `material.rate_applicability`.
- `src/febio_cae/domain/motion.py` provides immutable `MotionApplicability`, `MotionSample`, and `MotionProfile`. A profile requires an explicit named-frame `UnitDirection`, a same-frame `Point3` initial reference position, at least two supplied samples, strictly increasing nonnegative times, nonnegative nondecreasing displacement magnitudes, an explicitly supplied zero first displacement, and a positive final displacement. Plateaus are retained; samples are never sorted or synthesized.
- Motion applicability requires explicit nonempty quasi-static and rate-independent statements with evidence targets `motion.quasi_static_applicability` and `motion.rate_independent_applicability`. Cross-frame reference/direction mismatches reject.
- Public `from_dict` boundaries enforce schema version `1`, exact field sets, strict scalar types, and nested evidence/quantity validation. Persistent `to_dict` projections use SI quantity values; motion sample order remains chronological and ordered. `to_bytes` delegates to the existing canonical serializer.

These values describe supplied intent only. They do not establish registered authority, physical adequacy, readiness, solver permission, or native compatibility.

## Final local gates

All gates below ran at clean SHA `6f07c680322d252003fae8e5da2d4d39adc81d29`, with dirty state empty before and after.

| Record | Exact command | Result | Raw evidence |
|---|---|---|---|
| `P1-B2-gate-pytest-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B2-gate-pytest-01` | 143 passed, exit 0 | `.local/coordination/runs/P1-B2-gate-pytest-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-gate-format-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .` | 37 files formatted, exit 0 | `.local/coordination/runs/P1-B2-gate-format-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-gate-lint-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .` | all checks passed, exit 0 | `.local/coordination/runs/P1-B2-gate-lint-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-gate-mypy-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests` | no issues in 23 source files, exit 0 | `.local/coordination/runs/P1-B2-gate-mypy-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-gate-scanner-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` | PASS; 39 filesystem files, 39 index files, 0 issues, exit 0 | `.local/coordination/runs/P1-B2-gate-scanner-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-gate-build-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build` | sdist and wheel built, exit 0 | `.local/coordination/runs/P1-B2-gate-build-01/{metadata.json,stdout.bin,stderr.bin}` |

The scanner reported `git_tracking.available=true`, `excluded_tracked_files=0`, `issues=[]`, and the expected exclusions `.git`, `.local`, caches, `dist`, and `febio_cae.egg-info`.

## Wheel and installed smoke

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: c24530ee534a15d79c54efe6178aea2c5b6d75b381cf55595fc98ccfd7fa9470
Size: 26733 bytes
```

The fresh installation root was `.local/verification/P1-B2-installed-01`; the wheel was installed with `--no-index --no-deps --force-reinstall` into a new Python 3.12 venv.

| Record | Result | Raw evidence |
|---|---|---|
| `P1-B2-installed-preflight-01` | fresh install root absent, exit 0 | `.local/coordination/runs/P1-B2-installed-preflight-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-installed-wheel-hash-01` | SHA/size above, exit 0 | `.local/coordination/runs/P1-B2-installed-wheel-hash-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-installed-venv-01` | fresh Python 3.12 venv, exit 0 | `.local/coordination/runs/P1-B2-installed-venv-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-installed-pip-01` | `Successfully installed febio-cae-0.1.0`, exit 0 | `.local/coordination/runs/P1-B2-installed-pip-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-installed-cli-01` | `febio-cae 0.1.0`, exit 0 | `.local/coordination/runs/P1-B2-installed-cli-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-installed-import-01` | Python 3.12.10, isolated=1, `PYTHONPATH=None`, `PYTHONHOME=None`; `febio_cae`, `domain`, `material`, and `motion` imported from venv site-packages; metadata/module version `0.1.0`, exit 0 | `.local/coordination/runs/P1-B2-installed-import-01/{metadata.json,stdout.bin,stderr.bin}` |

Installed smoke is package/import evidence only; it does not establish solver, FBS, Studio, input-generation, or real-model success.

## Unverified items and next task

- P1 lifecycle work—case registration, issued-question generation, revision/state transitions, atomic persistence, and conflict/CAS behavior—is not implemented or verified here.
- Native FEBio/FBS/Studio compatibility, registry resolution, adapter mappings, actual applicability adequacy, real CAD/mesh inputs, authorized `02_CAE`, and BottomFrame real-model E2E remain unperformed.
- No rigid primitive, support, contact, aggregate CaseSpec, CLI, solver execution, or readiness/permission decision is included in this slice.
- PM independent exact-commit review, integration into `V2`, and any push are outside this worker handoff.

Next task: PM should perform the independent exact-commit review, then integrate only the reviewed clean commit sequence into `V2`.

## R1 semantic remediation addendum

Date: 2026-09-07

This addendum records the PM-directed R1 remediation after the original P1-B2 candidate. It is still synthetic/local contract evidence only. It does not establish real FEBio, official FBS, FEBio Studio, native, real-model, `02_CAE`, or BottomFrame success.

### R1 fixed Git boundary

| Item | Value |
|---|---|
| R1 starting code/report SHA | `ec36169f6567edf77d2d8f372b0205b60fa24d04` |
| R1 initial test-only SHA | `d3b29db488a57ea96315b7a0cf9cdcc65291973` |
| R1 test correction SHA | `6589d03dce1778aecf532185896d1b36ca301092` |
| R1 expanded test-only SHA | `3d1e84423a50b84a74d25f781255218e1da6a2c0` |
| R1 target-binding test-only SHA | `97885b9bc6af9cee8a67bfe91148b15650d7224c` |
| R1 production SHA | `d53d8579ccc96aca1296587d3503588c9107615c` |
| R1 final code candidate | `d53d8579ccc96aca1296587d3503588c9107615c` |
| authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote state | `REMOTE_CONFIGURED` |
| push/integration | not performed by this worker |

The R1 test-only commits changed only `tests/unit/contracts/test_motion.py` and `tests/unit/contracts/test_spatial.py`. The production commit changed only `src/febio_cae/domain/motion.py` and `src/febio_cae/domain/spatial.py`.

### R1 test-first evidence

The initial clean R1 RED was captured at `6589d03dce1778aecf532185896d1b36ca301092`. The exact command was:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_motion.py tests/unit/contracts/test_spatial.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B2-R1-red-01
```

`P1-B2-R1-red-01` collected 41 tests, had 2 behavioral assertion failures and 39 passes, exit 1. The fresh-basetemp preflight exited 0; there were no collection, setup, `KeyError`, `AttributeError`, or `TypeError` failures. Raw evidence is `.local/coordination/runs/P1-B2-R1-red-01/{metadata.json,stdout.bin,stderr.bin}`.

After the expanded regression set was committed at `3d1e84423a50b84a74d25f781255218e1da6a2c0`, `P1-B2-R1-red-02` collected 53 tests, with 25 failures and 28 passes, exit 1. This intentionally exposed the missing evidence constructor fields and missing `UnitDirection.from_dict`; it had no collection or environment failure. Raw evidence is `.local/coordination/runs/P1-B2-R1-red-02/{metadata.json,stdout.bin,stderr.bin}`. The subsequent narrow target-binding additions are in `97885b9`; all of them are covered by the clean GREEN below.

At clean production SHA `d53d8579ccc96aca1296587d3503588c9107615c`, the fresh-basetemp preflight `P1-B2-R1-green-basetemp-preflight-01` exited 0 and `P1-B2-R1-green-01` ran:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_motion.py tests/unit/contracts/test_spatial.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B2-R1-green-01
```

The result was 56 passed, exit 0. Raw evidence is `.local/coordination/runs/P1-B2-R1-green-01/{metadata.json,stdout.bin,stderr.bin}`.

### R1 contract changes

- `MotionProfile` now requires `direction_evidence`, `initial_reference_point_evidence`, and `history_evidence`. Each is immutable, serialized, restored at the public payload boundary, and bound to the exact targets `motion.direction`, `motion.initial_reference_point`, and `motion.history`. Missing, swapped, or misbound evidence rejects.
- `UnitDirection.from_dict` strictly validates the exact schema-1 field set, frame text, finite numeric components, and unit norm. It preserves already-normalized component floats without a second normalization pass, so direction payload round-trips retain canonical bytes, including subnormal-derived and PM-probe vectors.
- `MotionProfile.from_dict` delegates direction reconstruction to `UnitDirection.from_dict`; nested payload mutations do not mutate the immutable domain value, and evidence changes affect canonical bytes.

### R1 final local gates

All records below ran at clean production SHA `d53d8579ccc96aca1296587d3503588c9107615c`, with dirty state empty before and after.

| Record | Exact command and result | Raw evidence |
|---|---|---|
| `P1-B2-R1-gate-pytest-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B2-R1-gate-pytest-01`; 162 passed, exit 0 | `.local/coordination/runs/P1-B2-R1-gate-pytest-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R1-gate-format-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .`; 38 files formatted, exit 0 | `.local/coordination/runs/P1-B2-R1-gate-format-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R1-gate-lint-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .`; all checks passed, exit 0 | `.local/coordination/runs/P1-B2-R1-gate-lint-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R1-gate-mypy-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests`; no issues in 23 source files, exit 0 | `.local/coordination/runs/P1-B2-R1-gate-mypy-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R1-gate-scanner-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .`; PASS with 40 filesystem files, 40 index files, and 0 issues, exit 0 | `.local/coordination/runs/P1-B2-R1-gate-scanner-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R1-gate-build-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build`; sdist and wheel built, exit 0 | `.local/coordination/runs/P1-B2-R1-gate-build-01/{metadata.json,stdout.bin,stderr.bin}` |

### R1 installed wheel smoke

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 50eef2b8bf6b4b3854169065c42007c9043ac5c0cc340ad6199584b1b6950567
Size: 27280 bytes
```

The fresh installation root was `.local/verification/P1-B2-R1-installed-01`; the wheel was installed with `--no-index --disable-pip-version-check --no-cache-dir` into a new Python 3.12 virtual environment.

| Record | Result | Raw evidence |
|---|---|---|
| `P1-B2-R1-installed-preflight-01` | fresh install root absent, exit 0 | `.local/coordination/runs/P1-B2-R1-installed-preflight-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R1-installed-wheel-hash-01` | SHA/size above, exit 0 | `.local/coordination/runs/P1-B2-R1-installed-wheel-hash-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R1-installed-venv-01` | fresh Python 3.12 venv, exit 0 | `.local/coordination/runs/P1-B2-R1-installed-venv-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R1-installed-pip-01` | wheel installed successfully, exit 0 | `.local/coordination/runs/P1-B2-R1-installed-pip-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R1-installed-cli-01` | `febio-cae 0.1.0`, exit 0 | `.local/coordination/runs/P1-B2-R1-installed-cli-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R1-installed-import-01` | Python 3.12.10, isolated=1, `PYTHONPATH=None`, `PYTHONHOME=None`; `febio_cae`, `domain`, `motion`, and `spatial` imported from venv site-packages; metadata/module version `0.1.0`, exit 0 | `.local/coordination/runs/P1-B2-R1-installed-import-01/{metadata.json,stdout.bin,stderr.bin}` |

R1 does not change the unverified boundary: lifecycle/CAS/atomic persistence, native FEBio/FBS/Studio compatibility, registry resolution, physical applicability adequacy, real CAD/mesh inputs, authorized `02_CAE`, and BottomFrame real-model E2E remain unperformed. PM independent exact-commit review, V2 integration, and push remain outside this worker handoff.

## Report-stage postchecks

The report-stage checks were captured at code HEAD `6f07c680322d252003fae8e5da2d4d39adc81d29` with only this new report staged (`A  docs/reviews/2026-09-07-p1-b2-material-motion.md`). Both records retained the expanded argv, cwd `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness`, Python runner `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe`, timestamps, staged dirty state, and raw output paths.

| Record | Exact argv and captured result | Raw evidence |
|---|---|---|
| `P1-B2-report-diff-check-01` | `git diff --cached --check`; started `2026-09-07T07:12:02.156953Z`, finished `2026-09-07T07:12:02.197953Z`, exit 0; HEAD before/after `6f07c680322d252003fae8e5da2d4d39adc81d29`; staged dirty state before/after `A  docs/reviews/2026-09-07-p1-b2-material-motion.md` | `.local/coordination/runs/P1-B2-report-diff-check-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-report-scanner-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .`; started `2026-09-07T07:12:02.451952Z`, finished `2026-09-07T07:12:06.152658Z`, exit 0; PASS with 40 filesystem files, 40 index files, and 0 issues; HEAD before/after `6f07c680322d252003fae8e5da2d4d39adc81d29`; staged dirty state before/after `A  docs/reviews/2026-09-07-p1-b2-material-motion.md` | `.local/coordination/runs/P1-B2-report-scanner-01/{metadata.json,stdout.bin,stderr.bin}` |

These are completed report-integrity records for the staged report snapshot; they do not claim a clean post-commit tree, native capability, or physical adequacy. Product RED/GREEN and code gates were not rerun for report authoring.

## R1 final report-stage postchecks

After the R1 addendum was complete, the final staged report snapshot was checked with only this report staged (`M  docs/reviews/2026-09-07-p1-b2-material-motion.md`). The checks retain expanded argv, cwd, Python runner, timestamps, staged dirty state, and raw output paths in their metadata records.

| Record | Exact argv and captured result | Raw evidence |
|---|---|---|
| `P1-B2-R1-report-diff-check-final-01` | `git diff --cached --check`; exit 0; HEAD before/after `d53d8579ccc96aca1296587d3503588c9107615c` | `.local/coordination/runs/P1-B2-R1-report-diff-check-final-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R1-report-scanner-final-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .`; PASS with 40 filesystem files, 40 index files, and 0 issues, exit 0; HEAD before/after `d53d8579ccc96aca1296587d3503588c9107615c` | `.local/coordination/runs/P1-B2-R1-report-scanner-final-01/{metadata.json,stdout.bin,stderr.bin}` |

These final checks are report-integrity evidence only; they do not expand the synthetic/local, native, physical, lifecycle, or real-model verification boundary.

## R2 M2 initial-reference coordinate range remediation addendum

Date: 2026-09-07

The independent exact review of the original P1-B2 candidate reported three findings: H1 missing field-bound evidence, M1 non-stable direction restoration bytes, and M2 accepted-but-unserializable initial-reference coordinates. R1 addressed H1 and M1. This R2 addendum addresses only M2; it does not retroactively change the original or R1 records, and independent whole-candidate acceptance remains pending.

This remains synthetic/local contract evidence. It is not evidence of a real FEBio solve, official FBS, FEBio Studio, native capability, real model, authorized `02_CAE`, or BottomFrame success.

### R2 fixed Git boundary

| Item | Value |
|---|---|
| R2 starting R1 report SHA | `b2971b834f5dad8dcdd46b5818d63ca47173ad56` |
| R2 test-only SHA | `dcbae735098f1575dff90ecd933e9c6edb608723` |
| R2 test fixture correction SHA | `835702e05456f9bd5b9b1dba248fab74556563fe` |
| R2 production SHA | `2f4980ecc748544a6517fcbc0e46acdf95d4867e` |
| R2 final code candidate | `2f4980ecc748544a6517fcbc0e46acdf95d4867e` |
| authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote state | `REMOTE_CONFIGURED` |
| push/integration | not performed by this worker |

Only `src/febio_cae/domain/motion.py`, `tests/unit/contracts/test_motion.py`, and this existing report changed in R2. Units, spatial, canonical, material, evidence, and exports were not modified.

### R2 test-first RED / GREEN

The clean R2 test-only RED was captured at `dcbae735098f1575dff90ecd933e9c6edb608723`. Fresh-basetemp preflight `P1-B2-R2-red-basetemp-preflight-01` exited 0. The exact RED command was:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_motion.py tests/unit/contracts/test_spatial.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B2-R2-red-01
```

`P1-B2-R2-red-01` collected 69 tests, with 12 `DID NOT RAISE` assertion failures and 57 passes, exit 1. The failure classification is 9 actual intended range-behavior failures—all 6 direct-construction cases and the 3 from-dict overflow cases—and 3 incorrect from-dict underflow fixture expectations. The initial payload mutation changed only the underflow value while retaining the serialized SI unit `m`, so those three cases represented the valid, representable quantity `5e-324 m`, not the intended `5e-324 mm`. There were no collection, setup, `KeyError`, `AttributeError`, or `TypeError` failures. Raw evidence is `.local/coordination/runs/P1-B2-R2-red-01/{metadata.json,stdout.bin,stderr.bin}`.

The test-only correction `835702e05456f9bd5b9b1dba248fab74556563fe` made the intended invalid `mm` unit explicit in the three from-dict underflow payload cases. It changed no production behavior and was applied after the recorded RED, before the separate production commit. The corrected 12-case matrix is covered by the fresh GREEN below; no historical RED was rerun at `835702e`.

At clean production SHA `2f4980ecc748544a6517fcbc0e46acdf95d4867e`, fresh-basetemp preflight `P1-B2-R2-green-basetemp-preflight-01` exited 0 and `P1-B2-R2-green-01` ran the same focused command with basetemp `P1-B2-R2-green-01`. The result was 69 passed, exit 0. Raw evidence is `.local/coordination/runs/P1-B2-R2-green-01/{metadata.json,stdout.bin,stderr.bin}`.

### R2 production change

`MotionProfile.__post_init__` now calls the existing `Quantity.to_si()` for all three `initial_reference_point` coordinates at construction time. `MotionProfile.from_dict` reaches the same validation through the constructor. This preserves the existing quantity range policy: valid signed coordinates, true zero, and representable extremes remain valid; overflow and nonzero-to-zero underflow raise at the public entrypoint; no clamping, rounding, geometry normalization, or unit-policy change was introduced.

### R2 final local gates

All records below ran at clean production SHA `2f4980ecc748544a6517fcbc0e46acdf95d4867e`, with dirty state empty before and after.

| Record | Exact command and result | Raw evidence |
|---|---|---|
| `P1-B2-R2-gate-pytest-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B2-R2-gate-pytest-01`; 175 passed, exit 0 | `.local/coordination/runs/P1-B2-R2-gate-pytest-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R2-gate-format-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .`; 38 files formatted, exit 0 | `.local/coordination/runs/P1-B2-R2-gate-format-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R2-gate-lint-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .`; all checks passed, exit 0 | `.local/coordination/runs/P1-B2-R2-gate-lint-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R2-gate-mypy-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests`; no issues in 23 source files, exit 0 | `.local/coordination/runs/P1-B2-R2-gate-mypy-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R2-gate-scanner-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .`; PASS with 40 filesystem files, 40 index files, and 0 issues, exit 0 | `.local/coordination/runs/P1-B2-R2-gate-scanner-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R2-gate-build-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build`; sdist and wheel built, exit 0 | `.local/coordination/runs/P1-B2-R2-gate-build-01/{metadata.json,stdout.bin,stderr.bin}` |

### R2 installed wheel smoke

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: ab2a2b9f857dc070ef62b4863757a5cc8391865d6d4b5925b9636a908bea828b
Size: 27315 bytes
```

The fresh installation root was `.local/verification/P1-B2-R2-installed-01`; the wheel was installed with `--no-index --disable-pip-version-check --no-cache-dir` into a new Python 3.12 virtual environment.

| Record | Result | Raw evidence |
|---|---|---|
| `P1-B2-R2-installed-preflight-01` | fresh install root absent, exit 0 | `.local/coordination/runs/P1-B2-R2-installed-preflight-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R2-installed-wheel-hash-01` | SHA/size above, exit 0 | `.local/coordination/runs/P1-B2-R2-installed-wheel-hash-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R2-installed-venv-01` | fresh Python 3.12 venv, exit 0 | `.local/coordination/runs/P1-B2-R2-installed-venv-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R2-installed-pip-01` | wheel installed successfully, exit 0 | `.local/coordination/runs/P1-B2-R2-installed-pip-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R2-installed-cli-01` | `febio-cae 0.1.0`, exit 0 | `.local/coordination/runs/P1-B2-R2-installed-cli-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R2-installed-import-01` | Python 3.12.10, isolated=1, `PYTHONPATH=None`, `PYTHONHOME=None`; `febio_cae`, `domain`, `spatial`, and `motion` imported from venv site-packages; metadata/module version `0.1.0`, exit 0 | `.local/coordination/runs/P1-B2-R2-installed-import-01/{metadata.json,stdout.bin,stderr.bin}` |

R2 does not change the unverified boundary: lifecycle/CAS/atomic persistence, native FEBio/FBS/Studio compatibility, registry resolution, physical applicability adequacy, real CAD/mesh inputs, authorized `02_CAE`, and BottomFrame real-model E2E remain unperformed. This report records remediation evidence; it is not an independent acceptance verdict.

## R2 final report-stage postchecks

After the R2 addendum was complete, the final staged report snapshot was checked with only this report staged (`M  docs/reviews/2026-09-07-p1-b2-material-motion.md`). The checks retain expanded argv, cwd, Python runner, timestamps, staged dirty state, and raw output paths in their metadata records.

| Record | Exact argv and captured result | Raw evidence |
|---|---|---|
| `P1-B2-R2-report-diff-check-final-01` | `git diff --cached --check`; exit 0; HEAD before/after `2f4980ecc748544a6517fcbc0e46acdf95d4867e` | `.local/coordination/runs/P1-B2-R2-report-diff-check-final-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P1-B2-R2-report-scanner-final-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .`; PASS with 40 filesystem files, 40 index files, and 0 issues, exit 0; HEAD before/after `2f4980ecc748544a6517fcbc0e46acdf95d4867e` | `.local/coordination/runs/P1-B2-R2-report-scanner-final-01/{metadata.json,stdout.bin,stderr.bin}` |

These final checks are report-integrity evidence only; they do not expand the synthetic/local, native, physical, lifecycle, or real-model verification boundary.
