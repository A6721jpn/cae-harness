# Registered numerical criteria and explicit selection context

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

Review candidate only. `REMOTE_CONFIGURED`; no integration/push. Base: `113dda6c95ad756788cedd52a7448de4ca0b87f9` (R2 remains independently unaccepted). Test commits: `f186bd5986206839dd6d8670b93fc7e9aa20d9fa`, `400514f` (full SHAs in manifest). Production: `e16dc2e39ff7ec4568fc96e7952f90d4a4ec8bfb`. Final report-only successor SHA is in the local manifest/handoff; all clean production gates apply unchanged.

## Implemented boundary

A-private `MeshQualityRegistration` explicitly defines profile ID, positive finite length error bound, primitive kinds, algorithm/version and qualification evidence/scope. Canonical bytes determine the `NumericalProfileRef` digest. This is not a public domain record/JSON codec or new port. Only explicit synthetic qualification is supported in this slice; no native qualification or caller `SUPPORTED` authority exists.

Trusted `RegisteredCaseService.register_mesh_quality(case_id, record)` and `resolve_mesh_quality(case_id, ref)` use the case-local immutable registry. Exact ref/digest lookup verifies registered evidence source identity/kind/hash and actual content; IDs do not supply authority by themselves. Distinct digest versions can coexist, but a registered key is never replaced. Missing criteria do not inherit a tolerance from CompatibilityProfile or mesh size. Only mesh-quality dispatch changes; other numerical purposes remain on their existing route.

Freeze reserves a criteria/evidence-byte snapshot with the revision publication and generation. A prepared-publication flag requires that snapshot during finalization/recovery. Snapshot resolution requires the actual registered revision and matching generation/ref/current evidence; abandoned unpublished snapshots are cleaned with their publication so an ID can be retried. Execution rechecks the revision snapshot. Windows source/root protection is inherited from R2; unsupported platforms retain fail-closed behavior. The registered numerical payload is case-local rather than a new global profile framework.

Validation passes the current registered `GeometryIntent` only for a matching source digest/body/geometry path, while holding the source/generation serialization boundary. The test receiver checks the full explicit rotation/translation; it does not implement or prove C's transformed geometry calculations. Other selection paths receive no part transform and retain native-only semantics. There is no placement inference from a frame label.

## Exact common inventory — freeze for review

Only two common production files changed:

- `src/febio_cae/domain/artifacts.py`: import existing GeometryIntent; append `GeometrySelectionRequest.geometry_intent: GeometryIntent | None = None`; validate supplied type/source digest/body/geometry digest/target frame; serialize the field only when present. Existing absent/None v1 bytes are unchanged.
- `src/febio_cae/domain/codec.py`: `_selection_request` permits exactly that optional key and delegates its non-null value to the existing strict GeometryIntent decoder. Unknown fields still fail. No other decoder, record, schema version or port changed. Old readers may reject a new request; there is no silent downgrade.

Other paths: `src/febio_cae/application/service.py`, `src/febio_cae/storage/registry.py`, new private `src/febio_cae/storage/mesh_quality.py`, new `tests/component/application/test_mesh_quality_registration.py`, and `tests/component/application/test_persistence_authority.py` (existing positive synthetic fixture now explicitly registers numerical criteria). This report is the eighth path. No C/B implementation, CLI, dependency or packaging change.

## Evidence chronology

Absolute Python: `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe`. All repository commands below use cwd `C:/Users/backo/.codex/worktrees/8dd5/CAE-harness`; exact argv/raw streams/pre-post SHA/dirty state are retained by the local recorder.

- `AC-red-01`: `python -m pytest tests/component/application/test_mesh_quality_registration.py --basetemp .local/verification/AC-red-01`, 10 collected/10 failed, exit 1, at base113dda6 with dirty new tests. Two genuine behavioral failures: compatibility-only mesh authority was accepted; authoritative placement context was absent. The optional-field test failed on the missing constructor field; seven tests failed because the new private module did not exist. Those seven are scaffolding failures, not seven independent behavioral REDs. Test-only commit followed.
- `AC-green-01`: collection failed (exit 2) due to an intermediate indentation error; not a gate. `AC-green-02`: 2 failed/8 passed, exit 1 (EvidenceRef is not a public top-level codec type; a test also compared pre-normalized intent instead of stored intent). Fixed private decode uses the existing EvidenceRef constructor without expanding the shared codec.
- `AC-green-03`: focused 1 failed/63 passed, exit 1; exposed comparison of frozen objects rather than their canonical bytes. This development run is not final evidence. `AC-recovery-red-01`: 1 failed/1 passed/11 deselected, exit 1; genuine unpublished snapshot retry failure (orphaned ID), with production in progress. It is not clean-commit RED. Cleanup was repaired without overwriting a published snapshot.
- `AC-green-04`: 13 passed, exit 0, precommit. Tests cover positive registration/reopen/freeze; optional context and legacy codec bytes; missing/foreign/stale/unqualified/corrupt criteria; snapshot recovery/retry; generation and evidence tamper. Test-only follow-up400514f precedes productione16dc2e. No reset/amend/rebase occurred.

## Clean final gates at productione16dc2e

| Gate | Exact child command (python is the absolute executable above) | Result |
|---|---|---|
| Focused | `python -m pytest tests/component/application tests/component/storage tests/component/cli --basetemp .local/verification/AC-focused-final` | 67 passed in 75.33 s, exit 0 |
| Full | `python -m pytest --basetemp .local/verification/AC-full-final` | 1,115 passed in 94.14 s, exit 0 |
| Format | `python -m ruff format --check .` | exit 0 |
| Lint | `python -m ruff check .` | exit 0 |
| Types | `python -m mypy src tests` | 86 source files, exit 0 |
| CAE boundary | `python scripts/scan_cae_data.py --root .` | exit 0 |
| Build | `python -m build` | exit 0 |

Fresh external cwd `C:/Users/backo/.codex/verification/AC-installed-01`; PYTHONPATH/PYTHONHOME cleared; system Python `-I -m venv <external>/venv`, absolute venv Python `-I -m pip install --no-deps <repository>/dist/febio_cae-0.1.0-py3-none-any.whl`, installed `febio-cae.exe --version`, and absolute venv Python `-I <repository>/.local/coordination/ac_installed_consumer.py` all exit 0, clean pre/post e16dc2e. Version is `febio-cae 0.1.0`; module origins are venv site-packages. Eight CLI subprocesses cover connected create/inspect/spec/reopen and expected stale-generation/unavailable-native/initialization errors; the installed private provider also registers/reopens explicit synthetic criteria and rejects a stale ref. Expected child exits 8/4 are asserted boundaries, not successful analyses.

Wheel: 137,208 bytes, SHA256 `9e0538c0179e4257f76b304db16f582ff1f95d7db75b2a70730a7b3f18b08963`. Sdist: 107,259 bytes, SHA256 `e7b3536c90d20a96efa8700d9655a24cdaa866750977e7a8c1ad0b59036c69f7`. Stable copies and command/evidence hashes are indexed in `.local/coordination/ac-execution-manifest.json`. R2 records and candidate remain untouched; its old dist artifacts are preserved in PM's `P1-R2-PM-handoff-01/dist` rather than the reused build output paths.

## Deferred connections / next task

Independent exact-SHA review must freeze/accept this small common addition before C consumes it. No A-to-C private criteria projection, C geometry implementation, or mesh-recipe check changed. The current A policy-only recipe check and C's richer recipe still require C's final reviewed single preimage/function and A registered recomputation; deleting the authority check is not an integration solution.

A/B lifecycle composition remains OPEN. The synchronous byte producer is not RunnerPort start/poll/cancel/reconcile, and callback return does not prove native descendant drain. No RunnerPort/PollResult change or native wrapper was added. Complete installed analysis, native STEP/FEBio/FBS/Studio, full CT acceptance, real02_CAE and BottomFrame E2E remain unverified. All new evidence is synthetic/local; this is not P1/P2 or project completion.
