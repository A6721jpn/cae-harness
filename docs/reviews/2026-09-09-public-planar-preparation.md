# Public planar preparation source phase

The public `case --state-dir STATE prepare-planar CASE_ID --file REQUEST
--expected-generation N --json` command now composes current STEP inspection
and generation in an owned child, then publishes a case-local PREPARED origin.
This phase verifies source behavior with synthetic dependencies and Python-only
children. It does not establish native Gmsh/OCCT compatibility or real CAE success.

## Commits and scope

- Base: `54dc2c0927f0953925205f06262d4b27d48916b7`, accepted and pushed V2.
- Authority decisions: `f1ba67a`.
- Collected RED: `17fdce786e0c8f06495819063d0ae69c1bdf2475` and
  `fee2b61e27fc602f1450353f51fa121e711b186a`.
- Product: `3c667dce70904dc095579768292c4620eccda042`.
- Comment-only lint clarification: `4a3e7ece02d019cef65024a7354227889c4e97a6`.
- Final documentation commit is identified by the ignored handoff index.

`REMOTE_CONFIGURED`: origin is `https://github.com/A6721jpn/cae-harness.git`.
Local V2 and the local origin/V2 tracking reference both remain at the base;
this worker did not fetch, merge or push. Root AGENTS.md was not edited.
Independent exact-SHA review and PM integration remain pending.

Changed product files:

- `src/febio_cae/application/{_preparation.py,_preparation_request.py,service.py,_demo.py}`
- `src/febio_cae/adapters/geometry/preparation.py`
- `src/febio_cae/adapters/febio/_windows_job.py`
- `src/febio_cae/storage/{preparation.py,mesh_quality.py}`
- `src/febio_cae/cli/{main.py,case.py}`
- `tests/component/application/test_planar_preparation.py`

Documentation changes are the two authority documents, this report and its
review index. The storage implementation uses the repository's actual
`src/febio_cae/storage/` location.

## Behavior

Existing explicit specification/evidence requests are reused. Only current
inspection digests and corresponding part selection digests may start as null;
asserted mismatches are rejected. Physics and source meaning are not inferred.
Compatibility profiles resolve by registered digest; request JSON cannot issue
SUPPORTED status, a mesh, or a producer receipt.

The private production child requires Gmsh 4.15.2, OCCT 8.0.1 and AP214, records
module/build identity, checks planar faces and supports explicit box/AsPlaced.
Defaults are 600 seconds, one mesh generation, 100000 tetrahedra and 250000
nodes. Finite time/count overrides are explicit. Available logical CPUs respect
lower explicit bounds; the Windows job applies a byte limit equal to 80 percent
of available physical memory at operation start, bounded by total physical
memory. The owned child is terminated at its deadline, with exact cleanup
handles retained if cleanup cannot yet be confirmed. Other runner defaults are
unchanged. No solver is started by preparation.

The existing lease and generation CAS serialize PREPARING/FAILED/PREPARED
publication. Source/request/spec/evidence/snapshot/generation and current
inspection/backend/mesh/recipe/output identities are bound to the operation.
A freeze without successful final publication cannot supply an execution mesh
or pass the preflight entry. Existing demo origins retain their original path;
new origins use only their current producer output. This is not cross-database
rollback. Surface approximation remains UNVERIFIED.

## TDD and corrections

All runs used Python 3.12.10 from
`C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe`, with cwd
`C:/Users/backo/orca/workspaces/CAE-HARNESS-V2/step-admission-recovery` unless
the installed cwd is explicitly identified below. Ignored `.local/verification/`
contains each named record's raw `.log` and `.json` with expanded argv, cwd,
Python binary, before/after SHA and dirty state, UTC times and child exit code.

| Record | Exact command | Result |
|---|---|---|
| `public-red` | `python -m pytest tests/component/application/test_planar_preparation.py --basetemp .local/v/pr1` | 2 collected failures, exit 1 |
| `public-red-complete` | `python -m pytest tests/component/application/test_planar_preparation.py --basetemp .local/v/pr2` | 7 collected failures, exit 1 |
| `public-green-attempt7` | `python -m pytest tests/component/application/test_planar_preparation.py --basetemp .local/v/pg7` | 7 passed, exit 0 |

RED establishes absent command/module behavior; mismatch assertions did not
reach their later validation branches before implementation. Intermediate
attempts 1 through 5 failed and are retained, not counted as passes. Corrections
covered the synthetic fixture's schema, the SelectionRef key, required carrier
evidence, canonical revision reloading and the storage-wrapped publication
exception. Attempt 6 passed seven tests; attempt 7 additionally exercises
registered revalidation and explicit preflight refusal in the same cases.

The seven cases cover the actual public CLI through a private synthetic
dependency seam, source/geometry/version mismatch, incomplete publication,
malformed CLI input and a real Python-only child terminated by its wall budget.
They do not load native Gmsh or demonstrate native memory exhaustion handling.

## Required gates

| Record | Exact command | Result |
|---|---|---|
| `public-final-pytest` | `python -m pytest --basetemp .local/v/pf` | 1473 passed, exit 0, clean product SHA before/after |
| `public-final-format` | `python -m ruff format --check .` | 219 files formatted, exit 0 |
| `public-final-lint` | `python -m ruff check .` | TRY004, exit 1; superseded below |
| `public-final-types` | `python -m mypy src tests` | 172 source files, exit 0 |
| `public-final-boundary` | `python scripts/scan_cae_data.py --root .` | 221 tracked files, no issues, exit 0 |
| `public-final-lint-fixed` | `python -m ruff check .` | all checks passed, exit 0 |
| `public-final-format-fixed` | `python -m ruff format --check .` | 219 files formatted, exit 0 |
| `public-final-types-fixed` | `python -m mypy src tests` | 172 source files, exit 0 |
| `public-final-build` | `python -m build` | sdist and wheel built, exit 0 |

The full suite ran at `3c667dce70904dc095579768292c4620eccda042`.
The later product-file change adds only a TRY004 comment: an unsupported
registered input category is intentionally a ValueError at the CLI boundary.
No executable statement changed, so the full suite was not duplicated.
The corrected static gates and build ran clean at
`4a3e7ece02d019cef65024a7354227889c4e97a6`.

## Installed artifact

The exact wheel from `public-final-build.log` is
`dist/febio_cae-0.1.0-py3-none-any.whl`, SHA-256
`18c694bd7084fbf5eac28f478da3b294343cfbc07972669fbd00d57d870e4379`.

`python -m venv .local/verification/public-env` and that environment's
`Scripts/python.exe -m pip install --no-index` with the absolute wheel path
both exited 0. From fresh `.local/verification/public-cwd`, its
`Scripts/febio-cae.exe --version` returned `febio-cae 0.1.0`, exit 0.
Its Python ran `-I <absolute-public_installed_check.py>`, exit 0: CLI and new
preparation imports resolve under that environment's `Lib/site-packages`,
invalid public preparation input returns INVALID_INPUT/2, and an owned Python
child hits the 0.15-second deadline (observed 0.156 seconds) with no retained
cleanup obligation. Gmsh was not imported. Records: `public-venv`,
`public-install`, `public-installed-version`, `public-installed-boundary`,
`public-wheel-hash.json`.

## Remaining work

Next source task after exact Medium review and PM integration: connect the
public execution/preflight operation to this PREPARED origin, including finite
solver budget and existing ownership/recovery boundaries, using isolated
dependencies before any new native release. Successful real preparation,
general STEP support, native memory pressure, solver/FBS format-3 compatibility,
real LLM, required real E2E and final BottomFrame E2E remain unverified.
No real 02_CAE/BottomFrame input was read or changed, native tool installed or
started, or live LLM called in this phase. The project is not complete.
