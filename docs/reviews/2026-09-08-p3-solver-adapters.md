# P3 solver, result, quality, and preview adapter candidate

Date: 2026-09-08 (Asia/Tokyo)  
Branch: `codex/p3-solver-adapters`  
Remote state: `REMOTE_CONFIGURED` (`https://github.com/A6721jpn/cae-harness.git`, integration branch `V2`)  
Base: `c23dc59d4dae4810acd51fe2156b7d190d645acb`  
Production candidate before this report: `96b8891959c2db35ac9e20265d0f024e347b4375`

This candidate consumes the frozen common ports and stays inside the assigned adapter and
component-test ownership. It does not modify common domain records, application/storage/CLI
code, packaging metadata, real `02_CAE` data, or native solver/viewer state.

## Implemented boundary

- `CompilerAdapter` validates the registered profile, revision/mesh identity, canonical Tet10
  ordering, separate part/tool ownership, every declared selection-to-mesh mapping, output
  mapping, and required supported capabilities. It emits deterministic self-contained FEBio
  XML with materials, mesh, supports, rigid six-DOF intent, motion controller, contact
  orientation/numerics, and XPLT output requests. `LocalBundleStore` verifies staged bytes;
  it is a synthetic adapter-local store, not publication authority.
- `RunnerAdapter` consumes trusted owner context, makes one unique attempt root, launches the
  exact argv with `shell=False` and a new process group, records executable/cwd/argv/thread
  provenance, writes solver stdout/stderr logs, supports poll/cancel/reconcile, and reports
  root exit separately from descendant drain before `VALIDATING`.
- `XpltReaderAdapter` reads binary XPLT bytes, not JSON. The bounded supported subset is the
  observed bare FEBio magic, length-delimited blocks, version `0x35`, no compression, header,
  dictionary, node mesh, state times, and `VEC3F` fields. It binds output to the exact
  attempt and bundle, verifies file stability/digest/size, requires mapped variables and
  finite values, and applies the profile's explicit reaction sign mapping. Decoded values are
  kept in `LocalResultDataStore` as common `NumericResultData`; storage/publication remains
  outside this adapter.
- `QualityAdapter` retrieves actual numeric values through `ResultDataPort`, evaluates the
  declared `peak_abs_value`/`max_value` criterion, and returns separate `PASS`, `FAIL`,
  `UNVERIFIED`, or `NOT_APPLICABLE` statuses with measured values and applicability reasons.
  Missing output/state/data never becomes an implicit pass.
- `PreviewAdapter` checks the current registered XPLT digest before launch, uses the configured
  exact Studio `ToolIdentity`, requires an injected independent observation for confirmation,
  and returns a failed receipt when the file mutates after launch or confirmation.

## Changed files

Test commits:

- `9ed8a255b8749d05bb854c40dc834a5c66f87a2` — `tests/component/febio/__init__.py`,
  `fixtures.py`, `test_adapters.py`.
- `f35910f1e52c8f6cacb2ba03287edd7dcf311668` — expanded cross-record, lifecycle, and
  nonfinite-value component coverage.

Production commit:

- `96b8891959c2db35ac9e20265d0f024e347b4375` —
  `src/febio_cae/adapters/febio/{__init__,compiler,quality,runner,xplt_reader}.py` and
  `src/febio_cae/adapters/preview/{__init__,studio}.py`.

No other tracked files were changed by the candidate before this report.

## Test-first and gate evidence

All commands were run from `C:\Users\backo\.codex\worktrees\e081\CAE-harness` with the
Python 3.12 interpreter selected by the workspace. Each `--basetemp` directory was fresh.

| Gate | Exact command | Result | Exit |
|---|---|---:|---:|
| genuine RED | `python -m pytest tests\component\febio --basetemp .local\verification\P3-solver-red-01` | collected 10, failed 10 | 1 |
| component GREEN | `python -m pytest tests\component\febio --basetemp .local\verification\P3-debug-11 -q` | 12 passed | 0 |
| full tests | `python -m pytest --basetemp .local\verification\P3-full-01` | 1,060 passed in 23.65s | 0 |
| format | `python -m ruff format --check .` | 109 files already formatted | 0 |
| lint | `python -m ruff check .` | all checks passed | 0 |
| types | `python -m mypy src tests` | 77 source files, no issues | 0 |
| CAE boundary | `python scripts/scan_cae_data.py --root .` | 111 tracked files; 0 diagnostics/issues; status PASS | 0 |
| build | `python -m build` | wheel and sdist built | 0 |
| installed import | `python.exe -I -c "import febio_cae, febio_cae.adapters.febio.xplt_reader; ..."` from outside checkout | installed site-packages import, `installed-import-ok` | 0 |
| installed CLI | isolated venv `febio-cae.exe --version` | `febio-cae 0.1.0` | 0 |

The initial RED was a behavioral collection run, not a collection/setup error. The component
tests were then committed separately before the production commit. The final component suite
exercises compiler output and identity, unsupported capability, selection mapping loss,
truncation/mutation/wrong attempt/nonfinite XPLT, pre-drain reader rejection, owned process
poll/cancel/wrong-owner behavior, data-driven quality, and preview mutation invalidation.

## Artifacts and hashes

Built artifacts from the final production candidate:

- `dist/febio_cae-0.1.0-py3-none-any.whl` — SHA-256
  `7d5b43827e8190753740cd397ab44ca89512824b1cb033c289ee56cda4f17cd9`
- `dist/febio_cae-0.1.0.tar.gz` — SHA-256
  `52140b25b789be2c137dac1d8164689a899860c5f6a76d85d8c2d38fef09015a`

The final report-file hash is recorded in the PM handoff after the report commit so the report
content and hash remain consistent.

## Unverified and intentionally pending

- No real FEBio executable was launched. The compiler and XPLT bytes are synthetic component
  evidence only; they do not establish official FEBio input compatibility or solver success.
- No FEBio Studio process was launched. Preview confirmation uses an injected synthetic observer;
  it is not VW-01 evidence and does not establish an official Studio read confirmation.
- The XPLT implementation is deliberately bounded to the observed `0x35`/uncompressed/VEC3F
  subset. Compression, other versions, unsupported tags/storage, integration-point fields,
  and broader native layouts remain explicitly unsupported rather than inferred.
- The adapter-local bundle/result stores are not A's registered resolver, immutable publication,
  lifecycle CAS, ownership registry, or recovery service. Connected integration with A's P1
  service remains unverified.
- Geometry/meshing service integration, R1 readiness, CLI wiring, native profile registration,
  the new finite native synthetic specification/budget/output root, real E2E, VW-01, QA-01,
  and the final BottomFrame real-model E2E remain open. No native launch or `02_CAE` mutation
  was attempted under this work order.

This is a clean reviewed candidate for independent review and PM integration; it is not a
project-complete or native-qualified release.
