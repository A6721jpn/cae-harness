# Registered lifecycle and explicit CLI phase report

## Candidate

- Branch: `codex/p1-registered-cli`
- Integration state: `REMOTE_CONFIGURED`; authorized remote is the V2 repository and no push was performed.
- Accepted base: `c23dc59d4dae4810acd51fe2156b7d190d645acb`
- Final HEAD: `2944fd7d5fdcd1ee07fe7433940fd978b1f3b7f4`
- Final worktree: clean

The candidate adds the durable registered case service, explicit case CLI path, SQLite/filesystem registry, source/profile authority, publication recovery, ownership/publication checks, and component coverage from the accepted base. The final diff from the accepted base is limited to the application, CLI, storage, and focused component-test paths owned by this work order.

## Acceptance-gap repairs

The final production changes close both previously known gaps and five bounded preflight findings:

- Validation re-resolves every registered selection route and rejects adapter failures or a returned digest/body/frame outside the `SelectionRef` context.
- Profile validation requires the registered profile ID, the SHA-256 digest of the canonical `CompatibilityProfile`, and `SUPPORTED` capability status.
- Validation traverses nested physical `EvidenceRef` values and resolves their registered source identity, source kind, digest, and bytes; the top-level evidence envelope is not the only authority check.
- Prepared revision publication records the expected draft generation and draft ID. Recovery aborts a prepared CAS loser and removes only matching prepared bytes instead of registering stale authority.
- Revision targets are checked before file publication. Exact duplicate retries are integrity-checked and conflict-safe, so a rejected duplicate cannot replace an earlier immutable file.
- Owner validation binds case, run, attempt, and generation and persists the validated `AttemptRecord`. Manifest publication requires that registered attempt, its bundle digest, a validated read result, and present/digest-checked output bytes in the owned attempt path.
- Catalog and case-root reopen checks reject Windows reparse points, including directory junctions, rather than checking only `is_symlink()`.

## Test-first evidence

Before the five preflight repairs, the focused component command collected 26 tests, passed 21, failed 5, and exited 1. The failing cases were the nested-evidence, CAS-loser recovery, duplicate revision, owner/publication, and junction-root regressions. The command was:

```text
python -m pytest tests/component/storage tests/component/application tests/component/cli --basetemp .local/verification/P1-R1-red-07
```

The earlier selection/profile regression command collected 21 tests, passed 17, failed 4, and exited 1. The production corrections then made the focused suite 26/26 green; the final full suite below is the authoritative fresh result.

## Final gates at `2944fd7`

| Gate | Exact command | Result |
|---|---|---|
| Full tests | `python -m pytest --basetemp .local/verification/P1-R1-full-final-04` | 1,074 passed in 28.52 s, exit 0 |
| Format | `python -m ruff format --check .` | exit 0 |
| Lint | `python -m ruff check .` | exit 0 |
| Types | `python -m mypy src tests` | exit 0 |
| CAE boundary | `python scripts/scan_cae_data.py --root .` | exit 0 |
| Build | `python -m build` | exit 0 |
| Installed venv | `python -m venv .local/verification/P1-R1-installed-03/venv` | exit 0 |
| Wheel install | `.local/verification/P1-R1-installed-03/venv/Scripts/python.exe -m pip install --no-deps dist/febio_cae-0.1.0-py3-none-any.whl` | exit 0 |
| Installed CLI | `.local/verification/P1-R1-installed-03/venv/Scripts/febio-cae.exe --version` | `febio-cae 0.1.0`, exit 0 |

Built artifacts:

- `dist/febio_cae-0.1.0-py3-none-any.whl`: 123,983 bytes, SHA-256 `6b7c09586d78dc35877509f35de5b330fd2437396efcf8da5aceba7f9c314650`
- `dist/febio_cae-0.1.0.tar.gz`: 96,581 bytes, SHA-256 `9ce514e788e72e3a6502810320bee2eea5b7a6875cc140a10ac849b41138a256`

## Commit chronology

The functional test and production commits remain separate in the final history:

- `18d870d` — selection/profile authority regressions
- `bd5a050` — selection re-resolution and profile digest/status enforcement
- `70039c4` — five preflight authority-boundary regressions
- `016297a` — type annotation required by the nested-evidence regression
- `2944fd7` — five preflight production repairs

One local recording mistake occurred after the first green final gates: `05282ba` was accidentally amended with the five-line test typing annotation, producing mixed commit `96501fd`. Following PM correction, the exact corrective commands were `git reset --soft 70039c4`, `git reset HEAD -- src/febio_cae/application/service.py src/febio_cae/storage/catalog.py src/febio_cae/storage/registry.py`, then commits `016297a` and `2944fd7`. The original `05282ba` and `96501fd` objects remain identifiable in the local reflog; no product content was undone, and all final gates were rerun at `2944fd7`.

## Boundaries and next task

The synthetic geometry/profile adapters prove the registered service seam only. No actual STEP parser, FEBio, FEBio Studio, FBS, solver execution, native result reader, real `02_CAE` model, or BottomFrame real-model E2E was run or claimed. The installed external smoke verifies package installation and `--version`; it does not qualify native geometry/profile capabilities or a real freeze/run chain. Junction substitution was tested on this Windows host; arbitrary microsecond path races and hardlink attacks remain outside this phase.

This is a clean service/storage/CLI candidate for independent whole-candidate review and PM integration. The PM must run fresh integrated gates, connect accepted native adapters, and separately verify the required real CT-01/02/03 and BottomFrame E2E before declaring P1/R1 or the overall CAE project complete.
