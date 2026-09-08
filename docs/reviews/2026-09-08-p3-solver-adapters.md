# P3 solver adapters: corrected evidence and mixed-test migration

Date: 2026-09-08. Branch: `codex/p3-solver-adapters`.
REMOTE_CONFIGURED: `https://github.com/A6721jpn/cae-harness`
(the authorized repository; configured URL omits the optional .git suffix).
Integration branch: `V2`.

## Summary and decision

The mixed component tests now consume the registered native-layout synthetic
reader, canonical numeric codec, issued preview binding, and current compiler
and runner contracts. This is a test/report migration, not a new production
implementation or native qualification. Whole-candidate independent review
and acceptance remain pending; this report does not authorize integration.

Migration base: `3014b0da79f1c6f3486f688af2adfaeffc003c5b`.
The exact clean migration candidate and its fresh complete gate results are
recorded separately after commit in the ignored local verification manifest.
No pre-commit run below is described as a clean committed-source gate.

## Context, scope, and method

The decision is whether a coherent P3 candidate can enter whole-applicability
review. Product authorities remain the V2 design and greenfield plan.
Verification uses repository-local synthetic fixtures and bounded Python
processes. Synthetic reader/preview results are not native FEBio, official FBS,
Studio, Computer Use, or real-model evidence.

Only these tracked files change in this migration:

- `tests/component/febio/test_adapters.py`
- `tests/component/febio/test_r1_remediation.py`
- `tests/component/febio/mixed_fixture.py`
- this report

Existing standalone compiler, reader, runner, quality, and preview fixtures and
regressions are reused. No production, common-record, dependency, storage,
application, CLI, or real `02_CAE` changes belong to this migration.

## Historical evidence correction

The initial P3 report overstated the original RED and verification chronology.
The preserved independent audit records collection/import errors in both
original RED attempts; the second had ten ModuleNotFoundErrors. Those attempts
reused the RED basetemp and are **not genuine behavioral RED evidence**.

The original full GREEN returned 1,060 passed before the production commit:
20:51:41 UTC start, 20:52:06 result, 20:53:37 production commit output.
It was a dirty/pre-production-commit run, not a clean post-commit gate.
Missing historical metadata is not reconstructed. The original wheel/sdist
hashes do not establish identity with this later migration candidate.

The independent review's own exact-candidate 1,060-pass run was separate
evidence and did not retroactively repair TDD history. Its original candidate
was rejected for product findings. Later finite-slice RED/GREEN manifests
remain the relevant remediation evidence, with their inherited-dirty and
committed-source boundaries intact.

## Migration results and coverage

Before edits, the two inherited mixed files had SHA-256:

- `test_adapters.py`: `A6897CB5C1B289B5BEBAFE8626A7F8353CCCA4AD10DE6017D1F0B8B8952CB05F`
- `test_r1_remediation.py`: `9161F97C2992B17F2522277E082660D6A39B6E02F2D8C2DBB09E20E9F3C7FF5B`

Their inherited diff is preserved locally. All 25 collected mixed cases remain;
no valid test is skipped or removed. Old private XPLT identity tampering becomes
a foreign-attempt registered-source probe. Truncation and tool mismatch use
correctly hashed registered bytes to reach the intended parser boundary.
The quality negative uses correctly rehashed public-codec data with a foreign
execution binding. Preview negatives first establish an issued launch and
return a structured bound observation, including mutation during observation.

Compiler assertions check unnamed, disjoint node groups, native plot variables,
signed motion, numeric friction, and resolved set references. Negative-zero
checking matches complete zero tokens instead of rejecting legitimate -0.01
coordinates. Runner coverage retains wrong-owner, path escape, executable
registration, descendant writer growth during unavailable job accounting,
natural drain, cancellation, and an unrelated live sentinel. Cleanup uses
retained process/job handles.

Commands below ran at the migration base with the stated test-file edits:

| Stage | Exact command | Result | Exit |
| --- | --- | --- | --- |
| Baseline | `python -m pytest tests/component/febio/test_adapters.py tests/component/febio/test_r1_remediation.py --basetemp .local/verification/P3-mixed-baseline -q` | 12 failed, 13 passed | 1 |
| Migration 01 | same selection, `--basetemp .local/verification/P3-mixed-migration-01 -q` | 1 failed, 24 passed; test XML selector included non-var metadata | 1 |
| Migration 02 | same selection, `--basetemp .local/verification/P3-mixed-migration-02 -q` | 1 failed, 24 passed; old negative-zero substring assertion | 1 |
| Migration GREEN | `python -m pytest tests/component/febio/test_adapters.py tests/component/febio/test_r1_remediation.py --basetemp .local/verification/P3-mixed-migration-green -q` | 25 passed, zero skipped | 0 |

A diagnostic mypy invocation selecting only the three changed test files
could not resolve the src-layout package (27 import errors). It is not passing
type evidence; the required candidate gate is `python -m mypy src tests`.
No new product behavior is claimed from this test-only migration.
No physical calculation or material assumption is introduced.

## Verification plan and limitations

After the test/report commit, run one fresh complete required gate set:
`python -m pytest` with fresh basetemp, `python -m ruff format --check .`,
`python -m ruff check .`, `python -m mypy src tests`,
`python scripts/scan_cae_data.py --root .`, `python -m build` with a fresh
output directory, and a clean wheel installation with installed CLI version
and isolated synthetic numeric consumer. Record exact SHA, commands, counts,
exit codes, artifact hashes, source/installed identity, and clean status.

The next decision is exact whole-candidate review, including pending finite
preview and geometry-binding review outcomes. Local passing gates do not
establish publication authority, connected application/storage integration,
native solver/viewer qualification, or final BottomFrame E2E. Real E2E and
native evidence remain unverified; project completion is not claimed.

## References and evidence ledger

Published local evidence (not external physical data):

- `docs/specs/2026-08-27-febio-llm-cae-harness-design-v2.md` and
  `docs/plans/2026-08-27-febio-cae-harness-greenfield-plan.md`: product authority.
- Independent P3 product review M1: original collection errors and dirty GREEN
  chronology; its preserved audit is the source of the correction above.
- Finite compiler, runner issued-authority, reader, compiler-reference,
  quality, preview, and geometry-binding manifests: scoped remediation
  chronology. Their local paths and hashes are indexed by the migration
  manifest rather than copied into Git with development coordination state.
- Migration command records: direct local observations for the table above.

Inference: preserving coverage on the accepted contracts makes the candidate
coherent enough to request review; it does not itself establish acceptance.
