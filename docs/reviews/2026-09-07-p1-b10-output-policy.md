# P1-B10 output policy and evaluation intent

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

Date: 2026-09-07
Scope: FEBio CAE Harness V2 immutable output requests, saved result times, and explicit evaluation intent
Decision boundary: this report covers the local domain contract, static gates, package build, and installed-package smoke only.

This is synthetic/local evidence. It is not evidence of a native FEBio output vocabulary, native variable support, residual physical units, profile registration, registry trust, solver execution, result completeness, official FBS, FEBio Studio, real-model execution, `02_CAE`, or BottomFrame success. `quantity_id`, `measure_id`, `component_id`, and `aggregation_id` are semantic identifiers in an output profile contract; they are not native FEBio variable names, executable expressions, or proof that a requested combination is supported.

## Fixed Git boundary

| Item | Value |
|---|---|
| Worker branch | `codex/p1-b10-output-policy` |
| Accepted integrated P1-B9 base | `f554ed0bdff24b43f230688f9d9e0a9b61a8ce8c` |
| P1-B10 test-only contract SHA | `0106ae92f4394d86d3b7ee898f06ee471cac8e5f` |
| P1-B10 test-only time-boundary correction SHA | `882b25638a8b050e53b67b3c028a0f411066b7be` |
| P1-B10 test-only typing correction SHA | `fd7eb6a12c66235b412c0f58161b5ab80806247b` |
| P1-B10 production candidate | `6ed0dc7bceaa373764e531ca18ee0d70e15a89c6` |
| Authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| Remote state | `REMOTE_CONFIGURED` |
| Push/integration | not performed by this worker |
| PM integration worktree at handoff | `C:\Users\backo\.codex\worktrees\4063\CAE-harness`, clean `f554ed0bdff24b43f230688f9d9e0a9b61a8ce8c`, branch `codex/v2-pm-integration` |

The implementation change set before this report is limited to the three product paths below. The separate report commit adds only this report.

| Path | Change |
|---|---|
| `src/febio_cae/domain/output_policy.py` | new immutable output/evaluation policy contracts |
| `src/febio_cae/domain/__init__.py` | four public package exports |
| `tests/unit/contracts/test_output_policy.py` | synthetic contract tests |

No compiler, native reader, registry, CaseSpec, persistence, CLI, solver, FBS, Studio, or real-model paths were changed.

## Authority and native boundary

The design authority is `docs/specs/2026-08-27-febio-llm-cae-harness-design-v2.md`, with the P1 planning authority in `docs/plans/2026-08-27-febio-cae-harness-greenfield-plan.md`. This slice follows the common data, unit, and schema rules in design sections 5.1--5.3; the result extraction, metric, and quality boundaries in 8.1--8.3; the partial-change/comparison constraints in section 9; and the CT-01 contract gate in the plan.

The implementation deliberately stops before native or physical interpretation:

- `OutputRequest` records a semantic quantity, measure, component, requested location, explicit `SelectionRef`, result `FrameId`, known display-unit symbol, and exact evidence target. It does not resolve applicability, native names, coordinate transforms, ROI geometry, smoothing, or result units.
- `EvaluationRequest` records a semantic aggregation identifier, an explicit selection, and nonempty strictly increasing saved-state times. It does not execute an aggregation, interpolate, extrapolate, infer a maximum-stress acceptance rule, or authorize a result read.
- `OutputPolicy` requires an outputs-purpose `NumericalProfileRef`, a nonempty semantic request set, explicit saved times, and an explicit possibly-empty evaluation set. It validates references, geometry/body identity, and exact SI-equivalent saved-state membership without inventing initial/final states or physical breakpoints.
- Future profile, CaseSpec, compiler, result-reader, and quality services still have to verify support, ROI existence and coverage, required variables, native storage location, physical start/final/motion states, solver must-points, units, and quality applicability. A geometry convention or identifier spelling cannot substitute for that evidence.

## Implemented contract

All public values are frozen dataclasses. Constructors copy caller sequences, reject strings/bytes/mappings/sets where sequences are required, validate nested values and complete canonical serialization, and wrap structural/canonical failures as `OutputPolicyValidationError` with field context.

- `OutputRequest` requires `request_id`, `quantity_id`, `measure_id`, and `component_id` to match the strict ASCII identifier grammar `[A-Za-z_][A-Za-z0-9_]*`. `location` is exactly one of `node`, `element`, `integration_point`, `face`, `surface`, or `rigid_body`. The selection and frame are existing syntax-validated identity value types; registration and trust are resolved later. `display_unit` must be a known shared unit symbol with its exact spelling retained, and evidence must target exactly `outputs.requests.<request_id>`.
- `EvaluationRequest` requires strict identifiers for `evaluation_id`, `output_request_id`, and `aggregation_id`; an existing `SelectionRef`; an explicit nonempty ordered sequence of nonnegative time `Quantity` values; and evidence targeting exactly `outputs.evaluations.<evaluation_id>`. Times are checked after shared SI conversion, so duplicate SI instants and non-increasing order are rejected while equivalent units retain their explicit input identity.
- `OutputPolicy` requires an outputs-purpose `NumericalProfileRef`, at least one `OutputRequest`, explicit nonempty strictly increasing `saved_times`, and a possibly-empty sequence of `EvaluationRequest` values. Request and evaluation IDs must be unique and are canonically sorted by their own IDs. Every evaluation must reference a request; its selection must have the same `(geometry_digest, body_id)` pair as that request; and every evaluation state time must exist in `saved_times` after exact shared SI normalization.
- Nested face and resolution collections retain the existing canonical unordered semantics. Ordered time arrays and exact display-unit spelling remain explicit intent. No geometric subset is inferred, no raw face numbers are compared across geometry revisions, and different requested/result frames are retained for later registered transform validation.

## Test-first chronology and workspace ownership correction

The test-only contract was committed first at `0106ae92f4394d86d3b7ee898f06ee471cac8e5f`. The time-boundary fixture correction and typing-only fixture correction were subsequent test-only commits `882b25638a8b050e53b67b3c028a0f411066b7be` and `fd7eb6a12c66235b412c0f58161b5ab80806247b`. Production implementation was then committed at `6ed0dc7bceaa373764e531ca18ee0d70e15a89c6`.

The first P1-B10 execution records were discovered to have run in the PM worktree `4063`, despite the worker turn's workspace metadata naming `8dd5`. Those records are retained as actual `4063` evidence and are not relabeled as isolated-worker evidence. The PM preserved the incident material, including the prior P1-B10 records and raw streams under `C:\Users\backo\.codex\worktrees\4063\CAE-harness\.local\coordination\runs\` and the workspace incident record under `C:\Users\backo\.codex\worktrees\4063\CAE-harness\.local\verification\P1-B10-workspace-incident-01`. The PM tree was returned clean to `codex/v2-pm-integration` at `f554ed0bdff24b43f230688f9d9e0a9b61a8ce8c` before the isolated continuation.

The original availability RED remains the valid test-first RED; it was not rerun merely to change the execution cwd. The fresh 8dd5 runs below are post-production verification of the same clean candidate and establish that the implementation and tests are stable after the workspace correction.

### Original availability RED — actual 4063 evidence

`P1-B10-red-01` ran at test-only commit `0106ae92f4394d86d3b7ee898f06ee471cac8e5f` with this exact command:

```text
C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/unit/contracts/test_output_policy.py --basetemp C:/Users/backo/.codex/worktrees/4063/CAE-harness/.local/verification/P1-B10-red-01
```

It collected 96 tests and exited `1`: one intentional API-availability failure (`test_output_policy_api_is_available`) and 95 skips. There was no collection, setup, or environment failure. This is availability RED, not semantic behavioral evidence. The original stdout/stderr and metadata remain in the actual 4063 record directory.

### Preserved intermediate failures and closure — actual 4063 evidence

The original 31-record chronology also contains the following nonpassing intermediate checks. Their actual HEAD, dirty state, exit code, and raw streams are preserved; they are not silently counted as passing gates.

| Record | Actual HEAD and dirty-before state | Exit | Raw failure | Subsequent closure |
|---|---|---:|---|---|
| `P1-B10-test-draft-format-01` | `f554ed0bdff24b43f230688f9d9e0a9b61a8ce8c`; `?? tests/unit/contracts/test_output_policy.py` | 1 | Ruff reported the draft test file would be reformatted at multiple locations. | `P1-B10-test-draft-format-fix-01` reformatted it; `P1-B10-test-draft-format-02` then verified one file already formatted, exit 0. |
| `P1-B10-green-precommit-01` | `0106ae92f4394d86d3b7ee898f06ee471cac8e5f`; `M src/febio_cae/domain/__init__.py`, `?? src/febio_cae/domain/output_policy.py` | 1 | 94 passed and 2 intended time-boundary assertions failed because `1e-320 ms` was accepted instead of rejected. | Fixture correction commit `882b25638a8b050e53b67b3c028a0f411066b7be`; `P1-B10-green-precommit-02` passed 96, exit 0. |
| `P1-B10-production-lint-01` | `882b25638a8b050e53b67b3c028a0f411066b7be`; `M src/febio_cae/domain/__init__.py`, `?? src/febio_cae/domain/output_policy.py` | 1 | Ruff reported unsorted `__all__`, the `Set` import alias rule, and two duplicate-ID `min`/`sorted` findings. | Production lint fixes were followed by `P1-B10-production-lint-02`, which isolated one import-order finding, then `P1-B10-production-lint-03` passed, exit 0. |
| `P1-B10-production-lint-02` | `882b25638a8b050e53b67b3c028a0f411066b7be`; `M src/febio_cae/domain/__init__.py`, `M tests/unit/contracts/test_output_policy.py`, `?? src/febio_cae/domain/output_policy.py` | 1 | Ruff reported one unsorted import block in `output_policy.py`. | `P1-B10-production-lint-03` passed, exit 0; the final production candidate is `6ed0dc7bceaa373764e531ca18ee0d70e15a89c6`. |
| `P1-B10-production-mypy-01` | `882b25638a8b050e53b67b3c028a0f411066b7be`; `M src/febio_cae/domain/__init__.py`, `?? src/febio_cae/domain/output_policy.py` | 1 | Mypy reported four fixture helper `dict[str, object]` argument-type errors at tests 170 and 197. | The typing fixture correction was later committed as `fd7eb6a12c66235b412c0f58161b5ab80806247b`; `P1-B10-production-mypy-02` and `-03` passed, exit 0, while retaining their recorded dirty test/source state. |

Early wrapper attempts that reported a missing `run_command.py` were setup attempts pointed at the wrong 4063 path; they do not establish that the existing 8dd5 helper was absent and are not RED or gate evidence. The existing helper at `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\coordination\run_command.py` was subsequently used for every isolated record.

### Actual 4063 production gates — preserved historical evidence

The original production run at `6ed0dc7bceaa373764e531ca18ee0d70e15a89c6` passed its focused GREEN and all required gates in the actual 4063 worktree. It is reported here with its true cwd, not as an 8dd5 run.

| Record | Command/result (full argv is preserved in the actual 4063 record) | Exit |
|---|---|---:|
| `P1-B10-green-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/unit/contracts/test_output_policy.py --basetemp C:/Users/backo/.codex/worktrees/4063/CAE-harness/.local/verification/P1-B10-green-01`; 96 passed in 0.13s | 0 |
| `P1-B10-gate-pytest-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest --basetemp C:/Users/backo/.codex/worktrees/4063/CAE-harness/.local/verification/P1-B10-gate-pytest-01`; 701 passed in 16.59s | 0 |
| `P1-B10-gate-format-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff format --check .`; 67 files already formatted | 0 |
| `P1-B10-gate-lint-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff check .`; all checks passed | 0 |
| `P1-B10-gate-mypy-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m mypy src tests`; no issues in 41 source files | 0 |
| `P1-B10-gate-scanner-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe scripts/scan_cae_data.py --root .`; 69 tracked files, 0 diagnostics | 0 |
| `P1-B10-gate-build-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m build`; sdist and wheel built | 0 |

The full expanded argv, actual cwd, timestamps, Git state, and raw streams for these records are preserved under the 4063 coordination directory. They are not merged with the fresh 8dd5 record counts below.

## Fresh isolated 8dd5 verification

The isolated continuation used clean HEAD `6ed0dc7bceaa373764e531ca18ee0d70e15a89c6` in `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness`, with empty dirty state before and after each product command. Each record retains expanded argv, cwd, UTC timestamps, HEAD, dirty state, exit code, and raw stdout/stderr under `.local/coordination/runs/<record-id>/`.

| Record | Exact command/result | Exit |
|---|---|---:|
| `P1-B10-isolated-focused-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/unit/contracts/test_output_policy.py --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B10-isolated-focused-01`; 96 passed in 0.15s | 0 |
| `P1-B10-isolated-gate-pytest-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B10-isolated-gate-pytest-01`; 701 passed in 17.23s | 0 |
| `P1-B10-isolated-gate-format-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff format --check .`; 67 files already formatted | 0 |
| `P1-B10-isolated-gate-lint-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff check .`; all checks passed | 0 |
| `P1-B10-isolated-gate-mypy-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m mypy src tests`; no issues in 41 source files | 0 |
| `P1-B10-isolated-gate-scanner-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe scripts/scan_cae_data.py --root .`; 69 tracked files, 0 diagnostics | 0 |
| `P1-B10-isolated-gate-build-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m build`; sdist and wheel built | 0 |

The fresh suite and static gates confirm source/test stability after the cwd correction. They do not convert the synthetic tests into native or real-model evidence.

## Fresh wheel and outside-checkout installed smoke

The wheel produced by `P1-B10-isolated-gate-build-01` is:

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 14d163cb51fe3a7c92bde0de20a456fe27ec77932f769c0ebcb94e14bccd763b
Size: 48823 bytes
```

The hash was captured by `P1-B10-isolated-wheel-hash-01` at clean `6ed0dc7bceaa373764e531ca18ee0d70e15a89c6`. A new Python 3.12 venv was created outside both worktrees at `C:\Users\backo\AppData\Local\Temp\cae-harness-P1-B10-isolated-8dd5-01`. `P1-B10-isolated-outside-preflight-01` first verified that this root did not exist. Outside-cwd records used the 8dd5 checkout as `git-workdir` only for attribution; they did not write to the repository.

| Record | Result |
|---|---|
| `P1-B10-isolated-outside-preflight-01` | fresh outside root absent; exit 0 |
| `P1-B10-isolated-outside-venv-01` | new Python 3.12 venv; exit 0 |
| `P1-B10-isolated-outside-pip-01` | exact wheel installed offline with `--no-index --disable-pip-version-check --no-cache-dir --no-deps`; exit 0 |
| `P1-B10-isolated-outside-cli-01` | `febio-cae 0.1.0`; exit 0 |
| `P1-B10-isolated-outside-import-01` | isolated `-I`; version `0.1.0`; all four domain exports were identical between `febio_cae.domain` and `output_policy`; synthetic policy canonical projection succeeded with 2,256 bytes; all module origins were under the external venv and outside both checkouts; exit 0 |

The installed smoke proves packaging, import provenance, public export identity, and a synthetic canonical projection only. It does not prove output-profile registration, native result-variable support, XPLT/result-reader compatibility, physical unit normalization, ROI coverage, solver launch permission, result completeness, quality assessment, official FBS, FEBio Studio, or real-model success.

## Report-stage checks and handoff

Before the first report-only commit, the report was the only staged path while HEAD remained the clean production candidate `6ed0dc7bceaa373764e531ca18ee0d70e15a89c6`. The staged diff and scanner records below therefore belong to `6ed0dc7bceaa373764e531ca18ee0d70e15a89c6`, not to the later report commit.

| Record | Result at HEAD `6ed0dc7bceaa373764e531ca18ee0d70e15a89c6` |
|---|---|
| `P1-B10-isolated-report-diff-check-01` | `git diff --cached --check` exited 2 because two Markdown hard-break spaces were reported as trailing whitespace; not passing evidence. |
| `P1-B10-isolated-report-diff-check-02` | Corrected staged diff check exited 0 after those spaces were removed. |
| `P1-B10-isolated-report-scanner-01` | PASS, 70 tracked files, 0 diagnostics, exit 0. |
| `P1-B10-isolated-report-diff-check-03` | Final pre-commit staged diff check exited 0. |
| `P1-B10-isolated-report-scanner-02` | PASS, 70 tracked files, 0 diagnostics, exit 0. |

The first report-only commit was then created separately as `59bad9be0d05cc4111a954516afcabb783d3bbf3`. After the factual correction in this report, the final-text R1 checks below were run with the report again as the only staged path, at clean HEAD `59bad9be0d05cc4111a954516afcabb783d3bbf3`:

| Record | Result at clean HEAD `59bad9be0d05cc4111a954516afcabb783d3bbf3` |
|---|---|
| `P1-B10-isolated-R1-report-diff-check-01` | Final staged `git diff --cached --check`; exit 0. |
| `P1-B10-isolated-R1-report-scanner-01` | Final staged-report scan; PASS, 70 tracked files, 0 diagnostics, exit 0. |

The correction commit following these R1 checks is report-only. The final handoff is clean and contains exactly these four tracked P1-B10 paths relative to `f554ed0bdff24b43f230688f9d9e0a9b61a8ce8c`: `src/febio_cae/domain/output_policy.py`, `src/febio_cae/domain/__init__.py`, `tests/unit/contracts/test_output_policy.py`, and this report. Coordination records, temporary venvs, build outputs, and incident material remain outside the tracked product change set.

## Unverified items and remaining sequence

- Immutable output-profile records/catalog, declared quantity/measure/component/location/unit compatibility, registration and freshness, native variable dictionaries, XPLT storage mappings, and trusted result-reader behavior remain unimplemented or unverified.
- No physical stress/strain measure, result coordinate transform, ROI geometry, face/entity coverage, smoothing, interpolation, extrapolation, or native display-unit conversion is inferred or executed. No saved-time policy has been checked against a CaseSpec's physical start/final/motion breakpoints or a SolverPolicy's must-points.
- No required output/quality variable completeness, quality receipt, execution authority, lifecycle/CAS, persistence, artifact contract, FBS evidence, FEBio Studio load confirmation, native FEBio run, real CAD/mesh input, authorized `02_CAE` data, BottomFrame, or real-model E2E was performed.
- No schema freeze, native readiness, authorized execution, integration, push, or product completion claim is made by this slice.

Next sequence: independent exact whole review of the clean production candidate and this factual handoff; after an exact whole `ACCEPT`, PM-only integration into `V2` followed by fresh post-integration gates. This worker will not integrate, push, access native helpers, access real `02_CAE` data, or access BottomFrame.
