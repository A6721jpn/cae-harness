# STEP preparation admission prerequisites

This candidate adds optional admission settings to `GmshOCCConfig` and checks
them at both inspection and meshing entry points. AP214 must be declared by
the actual HEADER FILE_SCHEMA before loading the configured module; comments,
quoted text and DATA declarations do not supply that evidence. The owned
session checks exact OCC version evidence from `General.BuildInfo` and sets
`General.NumThreads` only when configured. Default behavior and the existing
return contracts remain unchanged. This is synthetic adapter evidence only.

## Commit and scope

- `REMOTE_CONFIGURED`: `https://github.com/A6721jpn/cae-harness.git`; integration
  branch `V2`. Observed local V2 and origin/V2 both point to
  `8a48f8e82978e19208c3d139578c1cc2af9031b2`; no fetch, integration or push by
  this worker.
- PM decision base: `ba04e4c6ef18b7ac0d88605592a1cfc3cbd2c477`.
- Test-first commit: `8073d25732ab0b7dfe51217a46257fb79ee1ec0b`.
- Product commit: `b5b234867ce30fe1fb2dd412dba5a54a5f2d6cbc`.
- Corrected implementation/test candidate:
  `3805433759bc93c39a264917c1b2fec51573cd30` (clean).
- Product/test files: `src/febio_cae/adapters/geometry/gmsh_occ.py` and
  `tests/component/geometry/test_gmsh_preparation.py`. This continuation changes
  only the test's dynamic kwargs annotation and loop-variable binding.
- Report changes: this file and the plan's section 13 status/next task text.
  No common schema, dependency, CLI or native implementation change.

## Recovery and RED/GREEN evidence

The original assigned checkout lost its Git binding before this continuation.
The retained candidate commits were verified in the same repository, and a
new child checkout was created at the exact product commit without launching
another agent. Surviving original files and evidence were preserved. Missing
or uncollected logs are not passing evidence. The original internal execution
is not retroactively classified as Orca-orchestrated execution.

The missing RED log was reconstructed in the isolated checkout at the exact
test-only commit, before returning to the product candidate. All eight tests
collected and failed because the configuration API did not yet exist
(`AttributeError`/`TypeError`); these are API-absence failures, not evidence
that invalid-input behavior was exercised before implementation.

Fresh raw records are in ignored `.local/verification/`: each named log has a
matching JSON record with argv, cwd, Python binary, before/after SHA and dirty
state, UTC start/end and exit code. The local handoff index gives absolute
paths and the recovery incident details.

| Record | Exact command (Python binary expanded in JSON) | Result |
|---|---|---|
| `red-reconstructed` | `python -m pytest tests/component/geometry/test_gmsh_preparation.py --basetemp .local/verification/step-admission-red-recovery-01` | 8 failed, exit 1 at test-only SHA |
| `green` | `python -m pytest tests/component/geometry/test_gmsh_preparation.py --basetemp .local/verification/step-admission-green-recovery-01` | 8 passed, exit 0 at corrected candidate |
| `lint-before` | `python -m ruff check .` | B023 loop binding, exit 1 at product SHA |
| `types-before` | `python -m mypy src tests` | 4 kwargs typing errors in one test, exit 1 at product SHA |

No additional test matrix was added. The retained eight tests use injected
fake Gmsh objects; they do not import or launch native Gmsh.

## Mandatory local gates

All records below target corrected candidate
`3805433759bc93c39a264917c1b2fec51573cd30`.

| Record | Command | Result |
|---|---|---|
| `all` | `python -m pytest --basetemp .local/verification/step-admission-all-recovery-01` | 1432 passed, 34 failed, exit 1; long-path file creation errors, not a pass |
| `all-short` | `python -m pytest --basetemp .local/v/a1` | 1118 passed, 348 setup errors, exit 1; missing basetemp parent, not a pass |
| `all-short-parent` | `python -m pytest --basetemp C:/Users/backo/orca/workspaces/CAE-HARNESS-V2/step-admission-recovery/.local/v/a2` | 1466 passed, exit 0, clean before/after |
| `format-clean` | `python -m ruff format --check .` | 213 files formatted, exit 0 |
| `lint` | `python -m ruff check .` | All checks passed, exit 0 |
| `types` | `python -m mypy src tests` | 167 source files, exit 0 |
| `boundary` | `python scripts/scan_cae_data.py --root .` | 215 files checked, no issues, exit 0 |
| `build-clean` | `python -m build` | sdist and wheel built, exit 0 |

The first format run failed on mixed working-file line endings. Formatting
and index refresh restored clean state without changing the committed blob;
`format-clean` is the passing record. The first build overlapped this
normalization and was superseded by `build-clean` on a clean checkout.
The short-basetemp retry initially lacked its parent directory; creating the
parent preceded the fresh `a2` run. Its raw setup errors were preserved. The
local logger's optional tail display could not decode that run's Windows
error text, but the raw log and JSON child exit had already been saved;
tail decoding was made tolerant without altering those records.

## Installed wheel

The exact wheel named by `build-clean.log` was
`dist/febio_cae-0.1.0-py3-none-any.whl`, SHA-256
`d0fe97e84b9c9e15c5c812804d694cf7d303a57cf3efd65a7203108cf8d3b918`.

A fresh Python 3.12.10 venv was created at
`.local/verification/installed-env`. Its Python installed that exact local
wheel with `-m pip install --no-index <absolute-wheel-path>` (exit 0).
From fresh `.local/verification/installed-cwd`, with PYTHONPATH removed,
its `Scripts/febio-cae.exe --version` returned `febio-cae 0.1.0` (exit 0).
Its Python ran `-I <absolute-installed_check.py>` (exit 0), verifying
`febio_cae`, `febio_cae.cli` and `febio_cae.adapters.geometry.gmsh_occ`
all resolve under that venv's `Lib/site-packages`. New settings default to
`None`, `False`, `None`, and `gmsh` was not imported. Raw records:
`venv`, `install`, `installed-version`, `installed-imports`, `wheel-hash.json`.

## Remaining work

Independent review of the exact final candidate and PM acceptance/integration
remain required. Next implementation task: finite-process-bound public
`prepare-planar` and producer-owned generation/publication record integration.
Public STEP CLI, native Gmsh/OCCT compatibility, mesh quality, independent
FBS-format-3 validation, real LLM, required real E2E and final BottomFrame E2E
remain unverified. No native tools were launched, native dependencies installed,
real model/02_CAE data modified, or live LLM called in this phase. The project
is not complete.
