# P1-B11 quality policy and numerical quality intent

Date: 2026-09-07
Scope: FEBio CAE Harness V2 immutable numerical quality criteria, thresholds, and applicability grounds
Decision boundary: this report covers the local domain contract, static gates, package build, and installed-package smoke only.

This is synthetic/local evidence. It is not evidence of an observed quality assessment, a result receipt, a native FEBio quality vocabulary, native variable support, profile registration, registry trust, solver execution, result completeness, official FBS, FEBio Studio, real-model execution, `02_CAE`, or BottomFrame success. `metric_id`, `parameter_id`, and `evaluation_ids` are semantic references in a future immutable quality profile and OutputPolicy; they are not native variable names, arbitrary expressions, physical limits, or proof that a criterion is applicable.

## Fixed Git boundary

| Item | Value |
|---|---|
| Worker branch | `codex/p1-b11-quality-policy` |
| Accepted integrated P1-B10 base | `40cc47f948acb1814e1b6e385f2bd4825b545f1d` |
| P1-B11 test-only contract SHA | `9c9d24f60e746aca1223ccb510a3a77bbcee4b96` |
| P1-B11 test-only collection correction SHA | `3ee0615744b020d91e12c95fc4ceeaf565f4fd4d` |
| P1-B11 test-only fixture correction SHA | `320ee4c7c8e4027c95d15e0f22596eb0ad661e82` |
| P1-B11 first production SHA | `d7c92beae52535a361e65807b3a41e6c0862bbb6` |
| P1-B11 final production candidate | `e156f0d0d5309df0c53018d497c62b7188a08c5a` |
| Authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| Remote state | `REMOTE_CONFIGURED` |
| Push/integration | not performed by this worker |

The implementation change set before this report is limited to the three product paths below. The report is a separate fourth tracked path.

| Path | Change |
|---|---|
| `src/febio_cae/domain/quality_policy.py` | immutable quality threshold, criterion, policy, and validation error |
| `src/febio_cae/domain/__init__.py` | four public package exports |
| `tests/unit/contracts/test_quality_policy.py` | synthetic contract tests |

No QualityAssessment, CaseSpec, profile resolver, result reader, native mapping, persistence, CLI, solver, FBS, Studio, or real-model paths were changed.

## Authority and boundary

The design authority is `docs/specs/2026-08-27-febio-llm-cae-harness-design-v2.md`, with the P1 planning authority in `docs/plans/2026-08-27-febio-cae-harness-greenfield-plan.md`. This slice follows the physical/calculation-spec boundary in design section 5.2, the result-quality obligations in section 8.3, and the CT-01 and QA-01 gates in the plan.

The implementation records intent only:

- `QualityThreshold` retains a typed `Quantity`, requires finite SI representability, and canonicalizes its projection to the shared SI value/unit. The parameter identifier does not determine dimension, range, comparison direction, or physical meaning. Signed and zero values remain valid generic numerical intent.
- `QualityCriterion` records a strict criterion ID, a semantic metric ID, explicit possibly-empty evaluation references, explicit possibly-empty typed thresholds, a nonempty applicability reason, and evidence targeting exactly `quality_policy.criteria.<criterion_id>`. Evaluation and threshold collections are immutable semantic sets sorted by their own IDs, with duplicates rejected even when content is equal.
- `QualityPolicy` requires a `NumericalProfileRef` with `purpose="quality"` and a nonempty immutable semantic set of criteria sorted by criterion ID. It performs no cross-object evaluation resolution; later CaseSpec validation must resolve `evaluation_ids` against the output policy.
- The trusted quality catalog/profile remains responsible for required criterion names, accepted parameter names/units/bounds, comparison methods, result variables, mesh-dependence targets, contact sign normalization, and applicability. A missing criterion cannot silently become `NOT_APPLICABLE`; the later observed assessment owns that status, reason, and result evidence.

The eventual catalog must enumerate completion/output finiteness, contact interval/penetration, rigid/support/contact compliance, equilibrium/residual, mesh-dependence, and physical applicability. This constructor does not assert that those obligations exist or are satisfied, and numerical thresholds cannot replace missing material limits, load grounds, or evaluation scope.

## Implemented contract

All public values are frozen dataclasses. Constructors copy caller sequences, reject strings/bytes/mappings/sets where sequences are required, validate nested values, and require complete canonical serialization. Structural and canonical failures are wrapped as `QualityPolicyValidationError` with field or canonical context.

- `QualityThreshold` requires a strict ASCII `parameter_id` matching `[A-Za-z_][A-Za-z0-9_]*` and a `Quantity` whose conversion to SI remains finite and nonzero when the input is nonzero. The original typed quantity is retained; canonical bytes use its SI projection.
- `QualityCriterion` validates strict ASCII `criterion_id`, `metric_id`, and every evaluation ID; retains empty evaluation/reference intent explicitly; sorts evaluation IDs and `QualityThreshold` values deterministically; rejects duplicate evaluation or threshold parameter IDs; validates clean nonempty applicability text without surrounding whitespace or control characters; and validates the dynamic evidence target.
- `QualityPolicy` validates the quality profile purpose, rejects empty criteria, copies and sorts criteria by ID, rejects duplicate criterion IDs, and canonicalizes the complete nested projection. Returned dictionaries are fresh projections and cannot mutate retained tuples or nested identity.
- No assessment flag, PASS/FAIL/UNVERIFIED/NOT_APPLICABLE status, result receipt, profile lookup, compatibility function, ROI/time/aggregation owner, native vocabulary, XML, reducer, or execution permission is introduced.

## Test-first chronology and preserved failures

The test-only contract was committed first as `9c9d24f60e746aca1223ccb510a3a77bbcee4b96`. A collection-time fixture correction was committed separately as `3ee0615744b020d91e12c95fc4ceeaf565f4fd4d`; a helper correction that kept invalid criterion identifiers independent from evidence-target construction was committed as `320ee4c7c8e4027c95d15e0f22596eb0ad661e82`. Production was then committed as `d7c92beae52535a361e65807b3a41e6c0862bbb6`, followed by the typing-only production correction `e156f0d0d5309df0c53018d497c62b7188a08c5a`.

Every wrapper record preserves expanded argv, actual cwd, UTC timestamps, HEAD, dirty state, exit code, and raw stdout/stderr under `.local/coordination/runs/<record-id>/`. The failed records below remain nonpassing evidence.

### Test draft, collection, and precommit failures

| Record | HEAD and dirty-before state | Exit | Failure and closure |
|---|---|---:|---|
| `P1-B11-test-draft-format-01` | `40cc47f948acb1814e1b6e385f2bd4825b545f1d`; `?? tests/unit/contracts/test_quality_policy.py` | 1 | Ruff reported the new test draft would be reformatted. `P1-B11-test-draft-format-fix-01` reformatted it; `P1-B11-test-draft-format-02` then passed. |
| `P1-B11-red-01` | `9c9d24f60e746aca1223ccb510a3a77bbcee4b96`; clean | 2 | Pytest collected 0 and stopped during collection because a parameterization invoked the unavailable API and triggered `pytest.skip` outside a test. This is a setup/collection failure, not RED evidence. The collection correction was committed as `3ee0615744b020d91e12c95fc4ceeaf565f4fd4d`. |
| `P1-B11-production-precommit-format-01` | `3ee0615744b020d91e12c95fc4ceeaf565f4fd4d`; `M src/febio_cae/domain/__init__.py`, `?? src/febio_cae/domain/quality_policy.py` | 1 | Ruff reported the package initializer needed formatting. `P1-B11-production-precommit-format-fix-01` corrected it; `P1-B11-production-precommit-format-02` then passed. |
| `P1-B11-green-precommit-01` | `3ee0615744b020d91e12c95fc4ceeaf565f4fd4d`; `M src/febio_cae/domain/__init__.py`, `?? src/febio_cae/domain/quality_policy.py` | 1 | 66 passed and 4 fixture-helper cases failed because invalid criterion IDs caused the helper to construct invalid evidence targets before the production validator ran. The test-only fixture correction was committed as `320ee4c7c8e4027c95d15e0f22596eb0ad661e82`, after which `P1-B11-green-precommit-02` passed 70. |

`P1-B11-test-draft-lint-01`, `P1-B11-production-precommit-lint-01`, `P1-B11-production-precommit-lint-02`, and the subsequent correction format/lint records exited 0. These setup and fixture corrections are preserved separately from the valid availability RED.

### Valid availability RED

`P1-B11-red-02` ran from the clean corrected test-only commit `3ee0615744b020d91e12c95fc4ceeaf565f4fd4d`:

```text
C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/unit/contracts/test_quality_policy.py --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B11-red-02
```

It collected 70 tests and exited `1`: one intentional API-availability assertion failure (`test_quality_policy_api_is_available`) and 69 skips. There was no collection, setup, or environment failure. This is availability RED, not semantic behavior RED.

## Production correction and clean gates

The first production candidate `d7c92beae52535a361e65807b3a41e6c0862bbb6` passed focused tests, format, lint, full pytest, and scanner, but `P1-B11-gate-mypy-01` exited `1` with four `attr-defined` errors because un-narrowed `object` collections were accessed as `QualityThreshold` and `QualityCriterion`. The smallest correction added explicit typed casts; correction format, lint, mypy, and focused records all exited 0. The final clean candidate is `e156f0d0d5309df0c53018d497c62b7188a08c5a`.

All final gate commands below ran from clean HEAD `e156f0d0d5309df0c53018d497c62b7188a08c5a` with empty dirty state before and after.

| Record | Exact command/result | Exit |
|---|---|---:|
| `P1-B11-green-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/unit/contracts/test_quality_policy.py --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B11-green-02`; 70 passed in 0.10s | 0 |
| `P1-B11-gate-pytest-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B11-gate-pytest-02`; 771 passed in 18.03s | 0 |
| `P1-B11-gate-format-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff format --check .`; 70 files already formatted | 0 |
| `P1-B11-gate-lint-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff check .`; all checks passed | 0 |
| `P1-B11-gate-mypy-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m mypy src tests`; no issues in 43 source files | 0 |
| `P1-B11-gate-scanner-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe scripts/scan_cae_data.py --root .`; PASS, 72 tracked files, 0 diagnostics | 0 |
| `P1-B11-gate-build-01` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m build`; sdist and wheel built | 0 |

The earlier `P1-B11-gate-mypy-01` failure at `d7c92beae52535a361e65807b3a41e6c0862bbb6` remains preserved and is not relabeled as a final pass.

## Wheel and outside-checkout installed smoke

The wheel produced by `P1-B11-gate-build-01` is:

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: f2b3b4bbd493a3b1fbe0d75a41ad7669b103f387aedaaad2bccc16c7e64e7a99
Size: 51109 bytes
```

`P1-B11-installed-outside-preflight-01` verified that `C:\Users\backo\AppData\Local\Temp\cae-harness-P1-B11-installed-01` did not exist. A fresh Python 3.12 venv was created there, outside both worktrees, and the exact wheel was installed offline.

| Record | Result |
|---|---|
| `P1-B11-installed-outside-preflight-01` | fresh outside root absent; exit 0 |
| `P1-B11-installed-outside-venv-01` | new Python 3.12 venv; exit 0 |
| `P1-B11-installed-outside-pip-01` | exact wheel installed with `--no-index --disable-pip-version-check --no-cache-dir --no-deps`; exit 0 |
| `P1-B11-installed-outside-cli-01` | `febio-cae 0.1.0`; exit 0 |
| `P1-B11-installed-outside-import-01` | isolated `-I`; all four quality exports identical between `febio_cae.domain` and `quality_policy`; synthetic canonical policy projection succeeded with 740 bytes; all module origins under the external venv and outside both checkouts; exit 0 |

Installed smoke proves packaging, import provenance, public export identity, and a synthetic quality-policy projection only. It does not establish profile registration, metric/parameter compatibility, observed quality status, native result-variable support, result-reader compatibility, physical applicability, solver execution, official FBS, FEBio Studio, or real-model success.

## Report-stage checks and handoff

After the final clean code candidate and gates, only this report was staged. The final staged checks are recorded after the report text was complete:

| Record | Result at clean HEAD `e156f0d0d5309df0c53018d497c62b7188a08c5a` |
|---|---|
| `P1-B11-report-diff-check-01` | `git diff --cached --check`; exit 0 |
| `P1-B11-report-scanner-01` | staged-report scan PASS; 73 indexed/tracked files, 0 diagnostics, exit 0 |

The report-only commit is separate from the test and production commits above. The final handoff must remain clean and contain exactly these four tracked paths relative to `40cc47f948acb1814e1b6e385f2bd4825b545f1d`: `src/febio_cae/domain/quality_policy.py`, `src/febio_cae/domain/__init__.py`, `tests/unit/contracts/test_quality_policy.py`, and this report. Coordination records, temporary venvs, build outputs, and real-data boundaries remain outside the tracked product change set.

## Unverified items and remaining sequence

- Quality profile/catalog registration, required metric and parameter declarations, units/bounds/comparison methods, evidence freshness, result-variable mappings, and trusted applicability remain unimplemented or unverified.
- No observed `QualityAssessment`, PASS/FAIL/UNVERIFIED/NOT_APPLICABLE state, result receipt, completion/output finiteness check, contact/rigid/support/equilibrium check, mesh-dependence comparison, contact sign normalization, or physical material/load applicability was performed.
- No cross-object resolution against OutputPolicy/CaseSpec, native solver/result-reader execution, authorized `02_CAE` data, FEBio Studio, official FBS, BottomFrame, or real-model E2E was performed.
- No schema freeze, native readiness, authorized execution, integration, push, or product completion claim is made by this slice.

Next sequence: independent exact whole review of this clean candidate and factual handoff; after an exact whole `ACCEPT`, PM-only integration into `V2` and fresh post-integration gates. This worker will not integrate, push, access native helpers, access real `02_CAE` data, or access BottomFrame.
