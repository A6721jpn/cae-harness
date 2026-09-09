# R2 residual repair — H2 design decision required

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

**BLOCKED / DO NOT INTEGRATE.** H1 numeric publication and H3 repeated freeze/edit behavior are repaired in the tested local seam. H2 rejects preexisting database aliases and detects links before subsequent SQL operations, but does not exclude a hardlink added inside a native SQLite call. A fresh clean-commit counterexample still changes the sibling file. Passing ordinary tests do not close this failure.

`REMOTE_CONFIGURED`; no integration/push. Base: `a4fec5451cb83a184809a90099495fddc02a4b26`. Test commits: `a8498b15989eac48939e6746cbad4df482f819a7`, `77938c2a0325c8aceb6ea6c7f3dd09c0e787cb29`. Production: `3f4ffcad285630af85f6b1de82864cc2569b71b8`. Final report-only successor is recorded in the execution manifest/handoff; clean production evidence applies unchanged. R2 and AC remain unaccepted; their previous candidate objects, reports, manifests and preserved artifacts remain intact.

## Delta and remaining boundary

- H1: the publishing SQLite transaction resolves every required observation's reader-issued numeric row and checks exact reference/digest/attempt/bundle/mapping, actual time axis and saved-time coverage, actual state count and compatible observation metadata before inserting the manifest. Reopened manifests recheck those rows. Foreign, missing, undercovered and wrong-axis payloads fail before publication; genuine two-state numeric publication/reopen remains usable. No numeric common record changed.
- H3: a private `current_frozen` row advances atomically with revision finalization/recovery. Patch acceptance uses that current revision, then checks it again inside draft CAS along with generation/replay protection. R1 -> patch -> freeze R2 -> fresh R2 patch works; old/replayed contexts fail and both immutable revisions remain readable. No attempt to infer/backfill an older database's previously unrecorded current-frozen pointer was added; a new freeze establishes it.
- H2 partial: one private SQLite helper now protects registry/catalog/profile databases. Main DB and journal/WAL/SHM file handles are checked for reparse/alias/identity problems before writable SQLite open and held through close. A separate main-file identity anchor rejects replacement on reuse. Connection statement/commit entry rechecks file identities/link counts. New DBs use PERSIST rollback journals so initial schema writes do not require deleting a pinned journal; existing WAL DBs remain WAL. FULL synchronous durability and SQLite locking remain enabled. Normal reopen, existing WAL data, competing publishers and root/source protections pass their tests.

**Unresolved H2:** Windows permits a new hardlink even while the rename-denying file pin is held. `RR-in-call-alias-final`, at clean production3f4ffca, installs a deterministic SQLite authorizer callback that creates a sibling hardlink during native INSERT, after the Python statement-entry guard. The INSERT succeeds and the sibling changes: before SHA256 `32a5f1865f668a06f68237db02f9d5270fdd8307b035802c2f2a0b608ed0598d`, after `d2762903da759a54c4e4901e09fac8f8b42a2b52f6697f86ae18aee46302cd26`. This is controlled fault injection in a dedicated synthetic area, not a native CAE run. It demonstrates the unexcluded in-call window; a post-call check would only detect an already completed write. The code/docstrings do not claim otherwise.

PM decision requested: authorize a bounded storage-commit redesign investigation (for example immutable database-snapshot publication), or define an independently enforceable filesystem-authority boundary. Neither alternative is implemented or qualified here. More path/link prechecks alone cannot establish the full no-aliased-write invariant. Do not relax acceptance merely because the ordinary suite passes. Further redesign is outside this small repair and needs PM scope/ownership direction.

## Exact changed paths

`src/febio_cae/application/service.py`; `src/febio_cae/storage/registry.py`, `catalog.py`, `profiles.py`, new `_sqlite.py`; `tests/component/application/test_registered_execution.py`, new `test_r2_residual.py`; this report. No delta to any common domain/codec/port file in this repair. The preceding AC optional GeometryIntent field and private mesh-quality registration are preserved, with C projection/max_elements/final recipe and A/B real runner composition still deferred.

## Test-first chronology

Absolute Python is `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe`; repository cwd is `C:/Users/backo/.codex/worktrees/8dd5/CAE-harness`. Completed recorder entries retain exact argv, pre/post SHA/dirty state, timestamps and raw streams.

`RR-red-01`: `python -m pytest tests/component/application/test_r2_residual.py tests/component/application/test_registered_execution.py --basetemp .local/verification/RR-red-01`, 9 failed/8 passed in 70.39 s, exit1 at a4fec with dirty tests; genuine behavior, then test-only commit. `RR-sqlite-green-01` failed on intermediate journal pinning (disk I/O error); `RR-sqlite-green-02` was interrupted/uncollected and is not passing evidence (separate local note, no fabricated metadata). `RR-green-01` had 1 failed/18 passed in 111.01 s because Windows did not forbid new links. The test now distinguishes blocked renames from next-statement alias rejection, while the stronger in-call failure is retained as an explicit independent failing probe. `RR-sqlite-green-04` passed the nine focused SQLite/edit tests. No reset/amend/rebase occurred.

## Final clean evidence at production3f4ffca

| Gate | Exact child command (python is the absolute executable above) | Result |
|---|---|---|
| Focused | `python -m pytest tests/component/application tests/component/storage tests/component/cli --basetemp .local/verification/RR-focused-final` | 80 passed, 174.62 s, exit0 |
| Full ordinary suite | `python -m pytest --basetemp .local/verification/RR-full-final` | 1,128 passed, 192.70 s, exit0 |
| Known H2 counterexample | `python .local/coordination/probe_rr_in_call_alias_final.py` | sibling changed, **exit1** |
| Format / lint | `python -m ruff format --check .`; `python -m ruff check .` | both exit0 |
| Types | `python -m mypy src tests` | 88 source files, exit0 |
| CAE boundary / build | `python scripts/scan_cae_data.py --root .`; `python -m build` | both exit0 |

Fresh external cwd `C:/Users/backo/.codex/verification/RR-installed-01`, with PYTHONPATH/PYTHONHOME cleared: absolute system Python `-I -m venv <external>/venv`; absolute venv Python `-I -m pip install --no-deps <repository>/dist/febio_cae-0.1.0-py3-none-any.whl`; installed `febio-cae.exe --version`; absolute venv Python `-I <repository>/.local/coordination/rr_installed_consumer.py`: all exit0 at clean production. Version0.1.0 and site-packages origins verified. The preserved consumer is reused in the new cwd: eight CLI subprocesses for create/spec/reopen and expected stale/unavailable/init errors, plus private synthetic quality registration/reopen/stale-ref rejection. It is not a complete installed analysis.

Stable wheel: 139,355 bytes, SHA256 `3d1eb7673b9eeb062f075953a009784ef42ba69c5347cc92a293c03039dbe829`; sdist: 109,184 bytes, SHA256 `abb80ee13a4502f59d41b494381b8f77a7a7b35c6809439dbc747087096a13c9`, under `.local/coordination/rr-artifacts`. `.local/coordination/rr-execution-manifest.json` indexes exact commands/raw hashes, interrupted-run note, clean failing probe, process journals, artifacts and final SHA.

Next action is the PM H2 design decision, then a fresh combined exact-SHA review when corrected. Native STEP/FEBio/FBS/Studio, real controller/descendant drain, C recipe/provider composition, complete installed analysis, full CT acceptance, real02_CAE and BottomFrame E2E remain unverified. This is a clean partial-repair handoff, not completion or acceptance.
