# P1-B3 support and rigid contract verification

Date: 2026-09-07
Scope: FEBio CAE Harness V2 P1-B3 immutable support and rigid-tool domain contracts
Decision boundary: this report covers local domain tests, static gates, package build, and installed-package smoke only.

This is synthetic/local evidence. It is not evidence of a real FEBio solve, official FBS, FEBio Studio, Gmsh, LLM, native-app, real-model, `02_CAE`, or BottomFrame success. No material, load, contact, ROI, or other physical meaning is inferred from geometry or naming conventions.

## Fixed Git boundary

| Item | Value |
|---|---|
| Working branch | `codex/p1-b3-support-rigid` |
| Integrated P1-B2 base | `d96189e6dcc15cfa7bd00d44d5c516cf0f7451c7` |
| test-only contract SHA | `fd740cc0913b9e4da072acdf920177438d879cca` |
| test-only fixture correction SHA | `96627014997fe8fa139b7d7af09a8e6ac99b1cf7` |
| original production SHA | `a2551f9a566960ac35943548b36215b66331557f` |
| R1 test-only remediation SHA | `5f28a20ec753bb92ab5569d6cfd080204bbe2eb8` |
| R1 production remediation SHAs | `97ba452091008f9dc7c7bd5f06c9a2e3e735fad6`, `0144b1a6b827f368c78d82b563a211916718baed` |
| R2 starting code/report SHA | `47bff4f3a16a3215406aca26dbee3ef00092bb39` |
| R2 production remediation SHA | `339336ce73ae78d6c3eb14b579de7e28e7fd6d8e` |
| final code candidate | `339336ce73ae78d6c3eb14b579de7e28e7fd6d8e` |
| authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote state | `REMOTE_CONFIGURED` |
| push/integration | not performed by this worker |

The original test-only commits add and correct only `tests/unit/contracts/test_support.py` and `tests/unit/contracts/test_rigid.py`. The original production commit adds `src/febio_cae/domain/support.py`, `src/febio_cae/domain/rigid.py`, and their explicit exports in the existing `src/febio_cae/domain/__init__.py`. R1 changes only `tests/unit/contracts/test_support.py` and `src/febio_cae/domain/support.py`; no rigid, selection, spatial, units, foundation, or export files changed in R1. R2 changes only `src/febio_cae/domain/support.py` and `src/febio_cae/domain/rigid.py`; no tests, selection, spatial, units, foundation, or export files changed in R2. The final code tree was clean after the R2 gates.

## Test-first RED / GREEN

The original worker-turn commands were direct PowerShell invocations captured from the source event stream; they were not executed through the later `run_command.py` recorder. The original event ordinals and item IDs are given where relevant below. R1 commands used `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe` through `.local/coordination/run_command.py`, which recorded argv, cwd, Python executable, HEAD, dirty state, timestamps, exit code, and raw stdout/stderr paths. The original PowerShell `python` commands were later resolved as Python 3.12.10 at event ordinal `6226`; that resolution is not retroactive per-command wrapper metadata.

### Availability RED

At clean test-only SHA `fd740cc0913b9e4da072acdf920177438d879cca`, both production modules were absent. The direct original command was captured at event ordinal `6065` (`exec-a84b553b-ea4c-4024-ad7e-596577fc1750`):

```text
python -m pytest tests/unit/contracts/test_support.py tests/unit/contracts/test_rigid.py --basetemp .local/verification/P1-B3-red-01 -q
```

It collected 39 tests, produced 2 API-availability assertion failures and 37 semantic skips, and exited `1`. There were no collection or environment failures. This is availability RED evidence. The original event stream is the provenance for this direct run; no `run_command.py` metadata record was created for it.

The initial implementation run at event ordinal `6138` (`exec-631ab0f6-5d3d-47d0-a811-3823a542c327`) produced `10 failed, 29 passed`, exit `1`, while formatting/import and the invalid placement evidence fixture (`"z"` is not lowercase hexadecimal) were still being corrected. It is an intermediate implementation failure, not the test-first behavioral RED. Test-only SHA `96627014997fe8fa139b7d7af09a8e6ac99b1cf7` corrected that fixture without changing production behavior.

### Original focused GREEN provenance correction

The original focused command was captured at event ordinal `6159` (`exec-ab2022eb-b3d7-426d-9be6-9b8f29b92fdf`) after the test-only correction. It ran with `HEAD=96627014997fe8fa139b7d7af09a8e6ac99b1cf7` and uncommitted production files (`src/febio_cae/domain/support.py`, `src/febio_cae/domain/rigid.py`, and the export edit) in the working tree; it therefore was not a clean production-SHA GREEN:

```text
python -m pytest tests/unit/contracts/test_support.py tests/unit/contracts/test_rigid.py --basetemp .local/verification/P1-B3-green-02 -q
```

The result was 39 passed, exit `0`. This historical result is retained as originally captured and is not relabeled as a clean candidate result.

### R1 remediation RED / GREEN

At clean test-only SHA `5f28a20ec753bb92ab5569d6cfd080204bbe2eb8`, the fresh-basetemp preflight record `P1-B3-R1-red-basetemp-preflight-01` exited `0`. The exact recorded RED command was:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_support.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B3-R1-red-01 -q
```

Recorded as `P1-B3-R1-red-01`, it collected 22 tests and produced 6 intended behavioral failures, 14 passes, and 2 skips, exit `1`. The failures were missing frame/transform-evidence slots, nested `SupportSet` semantic-set propagation, and four constructor-time nested range cases. There were no collection, setup, or environment failures. Raw evidence is `.local/coordination/runs/P1-B3-R1-red-01/{metadata.json,stdout.bin,stderr.bin}`.

The first remediation implementation run, `P1-B3-R1-green-01`, retained one genuine nested-collection failure (`21 passed, 1 failed`, exit `1`) because `SupportSet.to_bytes()` still lacked the nested selection paths. That record is preserved under `.local/coordination/runs/P1-B3-R1-green-01/`.

After that smallest fix, `P1-B3-R1-green-02` collected 22 tests and passed 22, exit `0`, on a fresh basetemp. The final clean production candidate was then recorded separately: at `0144b1a6b827f368c78d82b563a211916718baed`, `P1-B3-R1-green-final-01` again passed 22 tests, exit `0`, with clean HEAD/dirty-state metadata. Raw evidence is `.local/coordination/runs/P1-B3-R1-green-final-01/{metadata.json,stdout.bin,stderr.bin}`.

## Implemented contracts

- `SupportId` is an opaque support identifier. `SupportComponent` accepts only explicit `fixed` or `free` intent and an `EvidenceRef` targeting one of `support.x`, `support.y`, or `support.z`.
- `SolidSupport` requires a `support_surface` `SelectionRef`, explicit support frame, field-bound `support.frame` evidence, all three translational components, and exact component-to-evidence binding. If a transform is omitted, the selection and support frames must match and no orphan transform evidence is accepted. If supplied, the `RigidTransform` must map the selection frame to the support frame and carry field-bound `support.transform` evidence, including for identity transforms. No rigid-mode autofix or hidden constraint is synthesized; all-free intent remains explicit.
- `SupportSet` copies and sorts supports by semantic `SupportId`, rejects duplicate IDs, and serializes deterministically. Its parent projection consumes the canonical `SelectionRef.to_bytes()` projection, retaining equivalence for reordered face IDs/resolution faces without duplicating `SelectionRef`'s unordered-path policy. The public alias `SupportCollection` refers to the same contract.
- `RigidPrimitive` supports only `sphere`, `cylinder`, and `box`, with exact dimension sets and explicit conventions: sphere center at local origin, cylinder centered on local z axis, and box centered on local x/y/z axes. Every dimension is a positive length `Quantity`; constructor-time `to_si()` validation preserves the existing finite-range and underflow policy without clamping or rounding.
- Rigid placement requires a typed `BodyId`, `local_frame`, and `RigidTransform` whose source frame matches the local frame. Translation components are validated through `Quantity.to_si()` at construction, including explicit identity placement. Model, placement, and each dimension retain field-bound `EvidenceRef` values. Caller mappings are copied into immutable mappings.
- Complete `SolidSupport` construction invokes the existing nested `SelectionRef` and `RigidTransform` projections so overflow/underflow in transform translations, coordinate predicates, centroids, or areas rejects at construction rather than at later serialization. `to_dict` projects quantities in SI units and `to_bytes` delegates to the existing canonical serializer. No public `from_dict` boundary was added because the nested selection and spatial contracts do not yet provide a shared restore boundary.

## Original local-gate provenance correction

The original report labels `P1-B3-gate-*` were descriptive report labels, not actual `.local/coordination/run_command.py` metadata record IDs. The direct commands were captured in the source event stream at the ordinals below. A clean status/HEAD observation was made after the original run at ordinal `6219`; it was not a per-gate wrapper bounding call. The original full-gate results remain valid, but their provenance is corrected here:

| Original event | Direct command/result |
|---|---|
| `6175`, `exec-570dafd8-fec5-4e2b-9d8a-3efc69597fc5` | `python -m pytest --basetemp .local/verification/P1-B3-full-01 -q`; 214 passed, exit 0 |
| `6184`, `exec-48021bef-f534-4bff-807c-846668420146` | `python -m ruff format --check .`, `ruff check .`, `mypy src tests`, and `scripts/scan_cae_data.py --root .`; respectively 43 formatted, all checks passed, no issues in 27 source files, and scanner PASS with 45 files; all exit 0 |
| `6191`, `exec-4ce3fe34-cc3b-497b-8c0f-741ad38547b2` | `python -m build`; sdist and wheel built, exit 0 |

The commands used PowerShell's `python` resolution in their original argv, not the absolute interpreter path shown in this report's earlier table. Event ordinal `6226` later observed the resolution as `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe`, Python 3.12.10.

## R1 final local gates

All records below were captured through `.local/coordination/run_command.py` at clean final SHA `0144b1a6b827f368c78d82b563a211916718baed`; each record has empty `dirty_before` and `dirty_after`.

| Record | Exact command | Result |
|---|---|---|
| `P1-B3-R1-gate-pytest-02` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B3-R1-gate-pytest-02 -q` | 222 passed, exit 0 |
| `P1-B3-R1-gate-format-02` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .` | 44 files already formatted, exit 0 |
| `P1-B3-R1-gate-lint-02` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .` | all checks passed, exit 0 |
| `P1-B3-R1-gate-mypy-03` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests` | no issues in 27 source files, exit 0 |
| `P1-B3-R1-gate-scanner-02` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` | PASS; 46 files checked, 0 diagnostics, exit 0 |
| `P1-B3-R1-gate-build-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build` | sdist and wheel built, exit 0 |

Raw gate records are under `.local/coordination/runs/<record-id>/{metadata.json,stdout.bin,stderr.bin}`. The failed `P1-B3-R1-gate-mypy-01` record is retained: it was a single optional-evidence narrowing diagnostic before the separate `0144b1a` production correction.

## Original wheel and installed-smoke provenance correction

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 7761239dd2d3e1a8d365e6f580fc7d9170922d980ac197585e04573f027b7c68
Size: 30999 bytes
```

The original fresh installation root was `.local/verification/P1-B3-installed-smoke-01`; event ordinal `6200` installed the wheel into a new Python 3.12 virtual environment with `--no-deps`. That command ran from the repository cwd, did not use `-I`, and did not assert installed module origins. It supports CLI/package import smoke only, not isolated outside-checkout import provenance.

| Check | Result |
|---|---|
| venv creation | exit 0 |
| wheel installation | `Successfully installed febio-cae-0.1.0`, exit 0 |
| `febio-cae --version` | `febio-cae 0.1.0`, exit 0 |
| domain imports from the repository-cwd smoke command | `RigidPrimitive SolidSupport`, exit 0; not an isolated-origin assertion |

Installed smoke is package/import evidence only; it does not establish solver, FBS, Studio, input-generation, or real-model success.

## R1 wheel and outside-checkout installed smoke

The R1 wheel built at final SHA `0144b1a6b827f368c78d82b563a211916718baed` was:

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 3bbc83068fc148af7d79a2524ed23b9e4a30fd3e35a92990fed8cecb39b268fd
Size: 31533 bytes
```

The fresh R1 venv was created outside the checkout at `C:\Users\backo\AppData\Local\Temp\cae-harness-P1-B3-R1-installed-02`. The recorded preflight, venv creation, wheel installation, CLI, and isolated import records all have `exit_code=0`:

| Record | Result |
|---|---|
| `P1-B3-R1-installed-outside-preflight-01` | fresh outside root absent, exit 0 |
| `P1-B3-R1-installed-outside-venv-01` | new Python 3.12 venv, exit 0 |
| `P1-B3-R1-installed-outside-pip-01` | wheel installed with `--no-index --disable-pip-version-check --no-cache-dir --no-deps`, exit 0 |
| `P1-B3-R1-installed-outside-cli-01` | `febio-cae 0.1.0`, exit 0 |
| `P1-B3-R1-installed-outside-import-01` | `-I`, `isolated=1`, version `0.1.0`, and `SolidSupport`/`SupportId` imports asserted; all origins were under the outside venv site-packages and outside the checkout, exit 0 |

The first in-checkout `P1-B3-R1-installed-import-01` record is intentionally retained as a failed diagnostic: its venv was under `.local` inside the checkout, so the origin assertion correctly failed. It is not used as R1 success evidence. Raw records are under `.local/coordination/runs/`.

## R2 canonical-selection and rigid-convention remediation

R2 is a production-only cleanup and documentation slice; no new RED was required because the existing focused contract suite already covered the semantic-set bytes and rigid convention behavior. The clean pre-change baseline `P1-B3-R2-pre-green-01` ran at `47bff4f3a16a3215406aca26dbee3ef00092bb39` and passed 47 tests, exit 0. After the production commit `339336ce73ae78d6c3eb14b579de7e28e7fd6d8e`, the fresh clean focused record `P1-B3-R2-green-final-01` again passed 47 tests, exit 0. Both records used fresh basetemps and had empty dirty state before and after.

R2 removes the duplicated `_selection_unordered_paths` helper from `support.py`. `SolidSupport.to_dict()` now obtains the canonical JSON projection from `SelectionRef.to_bytes()` through the standard-library JSON decoder, and `SolidSupport.to_bytes()`/`SupportSet.to_bytes()` canonicalize only their parent projections. This leaves `SelectionRef` as the sole owner of selection semantic-set normalization while preserving existing support bytes and regression behavior. The public `RigidPrimitive` docstring now defines sphere, cylinder, and box local geometry precisely: origin centering, cylinder local-z extent `+/-height/2` and xy radius, box length/width/height axes and half-extents, plus the boundary that a primitive alone does not declare 6DOF constraints, contact, a full `rigid_tool`, or a `CaseSpec`; no pose is inferred.

### R2 final local gates

All records below were captured through `.local/coordination/run_command.py` at clean code SHA `339336ce73ae78d6c3eb14b579de7e28e7fd6d8e`; each record has empty `dirty_before` and `dirty_after`.

| Record | Exact command | Result |
|---|---|---|
| `P1-B3-R2-green-final-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_support.py tests/unit/contracts/test_rigid.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B3-R2-green-final-01 -q` | 47 passed, exit 0 |
| `P1-B3-R2-gate-pytest-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B3-R2-gate-pytest-01 -q` | 222 passed, exit 0 |
| `P1-B3-R2-gate-format-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .` | 44 files already formatted, exit 0 |
| `P1-B3-R2-gate-lint-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .` | all checks passed, exit 0 |
| `P1-B3-R2-gate-mypy-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests` | no issues in 27 source files, exit 0 |
| `P1-B3-R2-gate-scanner-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` | PASS; 46 files checked, 0 diagnostics, exit 0 |
| `P1-B3-R2-gate-build-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build` | sdist and wheel built, exit 0 |

Raw R2 gate records are under `.local/coordination/runs/<record-id>/{metadata.json,stdout.bin,stderr.bin}`.

### R2 wheel and outside-checkout installed smoke

The R2 wheel built at code SHA `339336ce73ae78d6c3eb14b579de7e28e7fd6d8e` was:

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: a5a574770b60180b9c58e450b6e9cfef676157a7f4bd3e5bc45363442c9e8ffe
Size: 31727 bytes
```

The fresh R2 venv was created outside the checkout at `C:\Users\backo\AppData\Local\Temp\cae-harness-P1-B3-R2-installed-01`. The successful preflight, venv creation, wheel installation, CLI, and isolated import records all have `exit_code=0`:

| Record | Result |
|---|---|
| `P1-B3-R2-installed-outside-preflight-02` | fresh outside root absent, exit 0 |
| `P1-B3-R2-installed-outside-venv-01` | new Python 3.12 venv, exit 0 |
| `P1-B3-R2-installed-wheel-hash-01` | SHA/size above, exit 0 |
| `P1-B3-R2-installed-outside-pip-01` | wheel installed with `--no-index --disable-pip-version-check --no-cache-dir --no-deps`, exit 0 |
| `P1-B3-R2-installed-outside-cli-01` | `febio-cae 0.1.0`, exit 0 |
| `P1-B3-R2-installed-outside-import-01` | `-I`, `isolated=1`, version `0.1.0`, and `RigidPrimitive`/`SupportId` imports asserted; all origins were under the outside venv site-packages and outside the checkout, exit 0 |

The initial `P1-B3-R2-installed-outside-preflight-01` record is intentionally retained as a failed setup diagnostic: the generated probe received an empty path and asserted against the current directory before any outside installation state was created. It is excluded from the R2 success evidence; the corrected `-02` preflight is the authoritative freshness check. Raw records are under `.local/coordination/runs/`.

Installed smoke remains package/import evidence only; it does not establish solver, FBS, Studio, input-generation, or real-model success.

### R2 report-stage postchecks

After the R2 addendum was complete, the final staged report snapshot was checked with only this report staged (`M  docs/reviews/2026-09-07-p1-b3-support-rigid.md`). The checks retain expanded argv, cwd, Python runner, timestamps, staged dirty state, raw output paths, and code SHA `339336ce73ae78d6c3eb14b579de7e28e7fd6d8e` in their metadata records.

| Record | Result |
|---|---|
| `P1-B3-R2-report-diff-check-final-02` | `git diff --cached --check`; exit 0 |
| `P1-B3-R2-report-scanner-final-02` | `scripts/scan_cae_data.py --root .`; PASS with 46 files checked, 0 diagnostics, exit 0 |

## Unverified items and next task

- Lifecycle/CAS and atomic persistence, case aggregation, contact, mesh/solver policies, native FEBio/FBS/Studio compatibility, registry resolution, and physical adequacy remain unimplemented or unverified in this slice.
- Real CAD/mesh inputs, authorized `02_CAE` data, BottomFrame, solver execution, official FBS evidence, and real-model E2E remain unperformed.
- The original independent H1/M1/M2 findings are addressed by the R1 slice, and the R2 canonicalization/documentation cleanup is locally verified. Independent final exact-commit review of the R2 candidate, integration into `V2`, and push remain outside this worker handoff.

Next task: the PM should perform the independent exact-commit review of `339336ce73ae78d6c3eb14b579de7e28e7fd6d8e`, integrate only the reviewed candidate into `V2`, and preserve the synthetic/local versus real E2E verification boundary.
