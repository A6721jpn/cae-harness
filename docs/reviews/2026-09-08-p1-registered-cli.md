# Registered lifecycle and explicit CLI phase report

## Candidate

- Branch: `codex/p1-registered-cli`
- Integration state: `REMOTE_CONFIGURED`; authorized remote is the V2 repository and no push was performed.
- Accepted base: `c23dc59d4dae4810acd51fe2156b7d190d645acb`
- Functional implementation SHA: `2944fd7d5fdcd1ee07fe7433940fd978b1f3b7f4`
- Previous report-only candidate: `c3525215141abedd0b58a34b0482ba86b975a7ad`; it is superseded by this documentation correction.
- Final worktree: clean after the report-only correction commit.

The functional candidate adds the durable registered case service, explicit case CLI path, SQLite/filesystem registry, source/profile authority, publication recovery, ownership/publication checks, and component coverage from the accepted base. The functional diff contains the following 14 product/test paths; this report is the 15th changed path in the report candidate:

```text
src/febio_cae/application/__init__.py
src/febio_cae/application/service.py
src/febio_cae/application/specs.py
src/febio_cae/cli/case.py
src/febio_cae/cli/main.py
src/febio_cae/storage/__init__.py
src/febio_cae/storage/catalog.py
src/febio_cae/storage/profiles.py
src/febio_cae/storage/registry.py
src/febio_cae/storage/state.py
tests/component/application/test_persistence_authority.py
tests/component/application/test_registered_service.py
tests/component/cli/test_case_cli.py
tests/component/storage/test_registered_store.py
docs/reviews/2026-09-08-p1-registered-cli.md
```

## Acceptance-gap repairs

The final production changes close both previously known gaps and five bounded preflight findings:

- Validation re-resolves every registered selection route and rejects adapter failures or a returned digest/body/frame outside the `SelectionRef` context.
- Profile validation requires the registered profile ID, the SHA-256 digest of the canonical `CompatibilityProfile`, and `SUPPORTED` capability status.
- Validation traverses nested physical `EvidenceRef` values and resolves their registered source identity, source kind, digest, and bytes; the top-level evidence envelope is not the only authority check.
- Prepared revision publication records the expected draft generation and draft ID. Recovery aborts a prepared CAS loser and removes only matching prepared bytes instead of registering stale authority.
- Revision targets are checked before file publication. Exact duplicate retries are integrity-checked and conflict-safe, so a rejected duplicate cannot replace an earlier immutable file.
- Owner validation binds case, run, attempt, and generation and persists the validated `AttemptRecord`. Manifest publication requires that registered attempt, its bundle digest, a validated read result, and present/digest-checked output bytes in the owned attempt path.
- Catalog and case-root reopen checks reject Windows reparse points, including directory junctions, rather than checking only `is_symlink()`.

## Test-first chronology

The recorded RED chronology must distinguish scaffolding/collection failures from genuine behavioral regressions. The first two runs are preserved evidence but are not genuine production RED gates:

- `P1-R1-red-01`: head `c23dc59d4dae4810acd51fe2156b7d190d645acb`, dirty `?? tests/component/`; command `python -m pytest tests/component/storage tests/component/application tests/component/cli --basetemp .local/verification/P1-R1-red-01`; 2 collected, 2 collection errors, exit 2. Both errors were `ModuleNotFoundError: No module named 'febio_cae.application'`. This was an import/scaffolding failure, not a behavioral RED.
- `P1-R1-red-02`: the same base and dirty state, with the same command using `P1-R1-red-02`; 9 collected, 9 failed, 0 passed, exit 1. Seven service/import/setup failures were the same missing application module and two CLI cases failed because `case` was not yet an argparse choice. This was still incomplete scaffolding, not a genuine production RED.
- `P1-R1-red-03`: head `8cafe16c947f64bfb144bca7c04af58b9ceb1d73`, dirty `M tests/component/application/test_persistence_authority.py`, with the same focused command and `P1-R1-red-03`; 21 collected, 17 passed, 4 failed, exit 1. The four genuine failures covered selection re-resolution, stale profile digest, and unsupported profile capability. This was a genuine behavioral RED, but it was run with a dirty test file; the later test commit was rewritten as `18d870d` and must not be described as a clean RED run.
- `P1-R1-red-07`: head `bd5a050c0e0566dbbe126d1fa89500370909180b`, dirty `M tests/component/application/test_persistence_authority.py` and `M tests/component/storage/test_registered_store.py`, with the same focused command and `P1-R1-red-07`; 26 collected, 21 passed, 5 failed, exit 1. The five genuine failures covered nested evidence, CAS-loser recovery, duplicate revision publication, owner/publication authority, and Windows junction-root rejection.

The focused suite became 26/26 green after the corresponding production repairs. These chronology records do not claim clean RED evidence where the metadata shows a dirty worktree. The full suite below is the authoritative fresh result at the functional SHA.

## Final gates at functional SHA `2944fd7`

| Gate | Exact command | Result |
|---|---|---|
| Full tests | `python -m pytest --basetemp .local/verification/P1-R1-full-final-04` | 1,074 passed in 28.52 s, exit 0 |
| Format | `python -m ruff format --check .` | exit 0 |
| Lint | `python -m ruff check .` | exit 0 |
| Types | `python -m mypy src tests` | exit 0 |
| CAE boundary | `python scripts/scan_cae_data.py --root .` | exit 0 |
| Build | `python -m build` | exit 0 |

The earlier installed-venv smoke used the repository as its working directory. It proved a wheel install and `--version`, but it was insufficient as external installation-isolation evidence and is not counted as the installed smoke below.

Built artifacts:

- `dist/febio_cae-0.1.0-py3-none-any.whl`: 123,983 bytes, SHA-256 `6b7c09586d78dc35877509f35de5b330fd2437396efcf8da5aceba7f9c314650`
- `dist/febio_cae-0.1.0.tar.gz`: 96,581 bytes, SHA-256 `9ce514e788e72e3a6502810320bee2eea5b7a6875cc140a10ac849b41138a256`

## Corrected external installed smoke

The corrected installed smoke used the external workdir `C:\Users\backo\.codex\verification\P1-R1-installed-external-01`, a fresh venv, the absolute system Python `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe`, the exact wheel above, `--git-workdir C:\Users\backo\.codex\worktrees\8dd5\CAE-harness`, and explicit `--unset-env PYTHONPATH --unset-env PYTHONHOME`. The wrapper was `python .local/coordination/run_command.py`; all records retained `head_before=head_after=c3525215141abedd0b58a34b0482ba86b975a7ad` and clean repository metadata.

- `P1-R1-external-venv-01`: child argv `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -I -X utf8 -m venv C:\Users\backo\.codex\verification\P1-R1-installed-external-01\venv`; exit 0.
- `P1-R1-external-install-01`: child argv `C:\Users\backo\.codex\verification\P1-R1-installed-external-01\venv\Scripts\python.exe -I -X utf8 -m pip install --no-deps C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl`; exit 0.
- `P1-R1-external-import-01`: the first absolute-venv probe exited 1 and is preserved as a failure. Its source called `importlib.metadata.version("febio-cae")` without importing `metadata`; stderr was `AttributeError: module 'importlib' has no attribute 'metadata'`. This failure was not counted as a pass.
- `P1-R1-external-import-02`: the corrected child argv used the same absolute venv Python with `-I -X utf8 -c`, imported `version` with `from importlib.metadata import version`, and asserted the external cwd, null `PYTHONPATH`/`PYTHONHOME`, and site-packages origins for `febio_cae`, `febio_cae.application`, `febio_cae.storage`, and `febio_cae.cli`; exit 0. Output reported version `0.1.0`, Python `C:\Users\backo\.codex\verification\P1-R1-installed-external-01\venv\Scripts\python.exe`, and all four origins under `...\venv\Lib\site-packages\`.
- `P1-R1-external-cli-01`: child argv `C:\Users\backo\.codex\verification\P1-R1-installed-external-01\venv\Scripts\febio-cae.exe --version`; output `febio-cae 0.1.0`, exit 0.

This external evidence verifies fresh-venv installation, import origin, environment isolation, and CLI version only. It does not qualify native geometry/profile capabilities or a real freeze/run chain.

## Commit chronology

The functional test and production commits remain separate in the final history:

- `18d870d` — selection/profile authority regressions
- `bd5a050` — selection re-resolution and profile digest/status enforcement
- `70039c4` — five preflight authority-boundary regressions
- `016297a` — type annotation required by the nested-evidence regression
- `2944fd7` — five preflight production repairs
- `c352521` — previous report-only candidate, superseded by the corrected report commit created from it

One local recording mistake occurred after the first green final gates: `05282ba` was accidentally amended with the five-line test typing annotation, producing mixed commit `96501fd`. The worktree was then locally rewritten before the later PM instruction to make no further history changes; that reset was not PM-authorized. The exact corrective commands were `git reset --soft 70039c4`, `git reset HEAD -- src/febio_cae/application/service.py src/febio_cae/storage/catalog.py src/febio_cae/storage/registry.py`, then commits `016297a` and `2944fd7`. The original `05282ba` and `96501fd` objects remain identifiable in the local reflog; no product content was undone, and all final gates were rerun at `2944fd7`.

## Boundaries and next task

The synthetic geometry/profile adapters prove the registered service seam only. The 1,074 unit/component tests include a two-thread CAS test, sequential one-use question consumption, publication failure injection at `after_prepare`, `file_write`, `file_replace`, and `after_commit`, plus path/link/junction checks. CT-01/CT-02/CT-03 remain local/component required boundaries, not native or real-model acceptance.

Unverified in this phase are independent-process concurrency, two concurrent question consumers, actual child-process crash boundaries, and complete CT-01/CT-02/CT-03 acceptance. No actual STEP parser, FEBio, FEBio Studio, FBS, solver execution, native result reader, real `02_CAE` model, or BottomFrame real-model E2E was run or claimed. Junction substitution was tested on this Windows host; arbitrary microsecond path races and hardlink attacks remain outside this phase.

Status: this is not P1 completion. It is a clean service/storage/CLI candidate for independent whole-candidate review and PM integration. The PM must run fresh integrated gates, connect accepted native adapters, and separately verify the required real CT-01/02/03 and BottomFrame E2E before declaring P1/R1 or the overall CAE project complete.
