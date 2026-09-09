# P1-B5 rigid kinematics and tool intent verification

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

Date: 2026-09-07
Scope: FEBio CAE Harness V2 P1-B5 immutable rigid-tool six-DOF intent and supplied-motion consistency
Decision boundary: this report covers local domain tests, static gates, package build, and installed-package smoke only.

This is synthetic/local evidence. It is not evidence of a real FEBio solve, official FBS, FEBio Studio, native profile support, geometry generation, mesh resolution, CaseSpec readiness, real-model, `02_CAE`, or BottomFrame success. No geometry digest is invented from a primitive, no surface is claimed resolved by this slice, and no physical or solver default is inferred.

## Fixed Git boundary

| Item | Value |
|---|---|
| Working branch | `codex/p1-b5-rigid-kinematics` |
| Accepted V2 base | `734dd6ef8b6e2e5bc7ddfab9263fe5c806253849` |
| test-only contract SHA | `93137e331e72e0b2f2947ee95341afecf118b887` |
| test-only formatting correction SHA | `5ba95c35b1d0bdf230f2b0cabe00ea6c9565c67b` |
| test-only direction-typing correction SHA | `76af39b2c15fe2e6876a3607854a49031f7ff73b` |
| first production SHA | `f97c731b7b1c182d77afb6017bf03646ffe39013` |
| final production SHA | `ce90fbd5046d97fb24bffe1ea1d752ba2fa0b570` |
| authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote state | `REMOTE_CONFIGURED` |
| push/integration | not performed by this worker |

The tracked implementation changes are limited to the four authorized paths: new `src/febio_cae/domain/rigid_kinematics.py`, exports in `src/febio_cae/domain/__init__.py`, new `tests/unit/contracts/test_rigid_kinematics.py`, and this report. Existing `rigid.py`, `motion.py`, `contact.py`, spatial/selection foundations, geometry services, mesh/solver mapping, CLI, persistence, and native adapters were not changed.

## Implemented contract

- `RigidDofComponent` accepts only explicit `fixed`, `free`, or `prescribed` state and an `EvidenceRef`. `RigidDofSpecification` requires all six components—translation `x/y/z` and rotation `rx/ry/rz`—in one named `FrameId`, with component evidence bound to `rigid_tool.x`, `rigid_tool.y`, `rigid_tool.z`, `rigid_tool.rx`, `rigid_tool.ry`, and `rigid_tool.rz`, plus `rigid_tool.frame` evidence. Omission, unknown fields, wrong types, and wrong targets fail; no all-fixed default or rigid-mode stabilization is synthesized.
- `RigidToolIntent` composes an existing `RigidPrimitive`, a `SelectionRef` with exact role `tool_contact_surface`, the DOF specification, and field-bound `rigid_tool.contact_surface` evidence. It requires selection body identity and frame to match the primitive body and `placement.target_frame`, and requires the DOF frame to match that same target frame. It does not store a motion history, infer a geometry digest, claim generated-surface resolution, or bind a `CaseSpec`.
- Nested primitive bytes, the established canonical `SelectionRef` projection, and DOF bytes are forced at construction. Caller state is immutable and the canonical parent bytes preserve nested face/resolution semantic-set normalization. The primitive's explicit dimensions, placement, model evidence, and placement evidence remain authoritative within the composed projection.
- `check_translational_indentation_compatibility(tool, motion)` is a pure result-producing check. It requires a common frame, explicit fixed rotation on all `rx/ry/rz`, prescribed state on every exactly nonzero direction component, and fixed or prescribed state on each exactly zero direction component. It uses exact zero comparison, allows oblique directions to prescribe multiple global translations from the one supplied `MotionProfile`, preserves all input tags, and returns a precise unsupported reason without rewriting intent or claiming native readiness. `MotionProfile` remains the sole owner of direction, reference point, monotonic samples, and applicability.

## Test-first RED / GREEN

The exact wrapper records below retain expanded argv, cwd, Python runner, UTC timestamps, HEAD, dirty state, exit code, and raw stdout/stderr paths under `.local/coordination/runs/`.

### Availability RED

At clean test-only SHA `93137e331e72e0b2f2947ee95341afecf118b887`, `P1-B5-red-01` ran the requested fresh-basetemp command:

```text
C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/unit/contracts/test_rigid_kinematics.py --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B5-red-01
```

It collected 47 tests and exited `1` with one intentional API-availability assertion failure and 46 skips. The only failure was `P1-B5 rigid kinematics module is not available`; there were no collection, setup, or environment failures. This is availability RED, not a semantic behavioral failure.

The test-only formatting SHA `5ba95c35b1d0bdf230f2b0cabe00ea6c9565c67b` and direction-typing SHA `76af39b2c15fe2e6876a3607854a49031f7ff73b` are preserved as test-only corrections before production implementation. They do not change the contract cases.

### Focused GREEN chronology

Production commit `f97c731b7b1c182d77afb6017bf03646ffe39013` added the new module and exports. `P1-B5-green-01` collected and passed all 47 tests, exit `0`, with empty dirty state before and after.

The first full gate pass at `f97c731` exposed only an export-order lint defect: `P1-B5-gate-lint-01` exited `1` with Ruff `I001` and `RUF022` in `domain/__init__.py`. No test, format, type, scanner, or build failure was present. Production correction `ce90fbd5046d97fb24bffe1ea1d752ba2fa0b570` sorted those exports without changing the contract implementation.

At clean final production SHA `ce90fbd5046d97fb24bffe1ea1d752ba2fa0b570`, `P1-B5-green-02` ran:

```text
C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/unit/contracts/test_rigid_kinematics.py --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B5-green-02
```

It collected and passed all 47 tests in 0.13 seconds, exit `0`, with empty dirty state before and after.

## Final local gates

All final records below ran at clean production SHA `ce90fbd5046d97fb24bffe1ea1d752ba2fa0b570`; each has empty `dirty_before` and `dirty_after`.

| Record | Exact command | Result |
|---|---|---|
| `P1-B5-gate-pytest-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m pytest --basetemp C:/Users/backo/.codex/worktrees/8dd5/CAE-harness/.local/verification/P1-B5-gate-pytest-02` | 296 passed in 16.57s, exit 0 |
| `P1-B5-gate-format-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff format --check .` | 51 files already formatted, exit 0 |
| `P1-B5-gate-lint-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m ruff check .` | all checks passed, exit 0 |
| `P1-B5-gate-mypy-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m mypy src tests` | no issues in 31 source files, exit 0 |
| `P1-B5-gate-scanner-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe scripts/scan_cae_data.py --root .` | PASS; 53 files checked, 0 diagnostics, exit 0 |
| `P1-B5-gate-build-02` | `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe -m build` | sdist and wheel built, exit 0 |

The preserved intermediate records are not silently counted as final evidence: `P1-B5-gate-pytest-01` was 296 passed, `P1-B5-gate-format-01` passed with 51 formatted files, `P1-B5-gate-mypy-01` passed with 31 source files, `P1-B5-gate-scanner-01` passed with 53 files and 0 diagnostics, and `P1-B5-gate-build-01` exited `0`; only `P1-B5-gate-lint-01` required the export-order correction.

## Wheel and outside-checkout installed smoke

The wheel built at final production SHA `ce90fbd5046d97fb24bffe1ea1d752ba2fa0b570` was:

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 6ae7eae59999181f9444160b7a2298eceeea699a359116ec5e31c6a08c2acb30
Size: 37323 bytes
```

A fresh Python 3.12 venv was created outside the checkout at `C:\Users\backo\AppData\Local\Temp\cae-harness-P1-B5-installed-01`. All records below have exit `0`; the outside-cwd records used the real checkout as `git-workdir`.

| Record | Result |
|---|---|
| `P1-B5-installed-wheel-hash-01` | SHA/size above |
| `P1-B5-installed-outside-preflight-01` | fresh outside root absent |
| `P1-B5-installed-outside-venv-01` | new Python 3.12 venv |
| `P1-B5-installed-outside-pip-01` | exact wheel installed offline with `--no-index --disable-pip-version-check --no-cache-dir --no-deps` |
| `P1-B5-installed-outside-cli-01` | `febio-cae 0.1.0` |
| `P1-B5-installed-outside-import-01` | isolated `-I`, version `0.1.0`, rigid-kinematics exports and alias identities asserted; `febio_cae`, `domain`, and `rigid_kinematics.py` origins all under outside venv `site-packages` and outside the checkout |

Installed smoke is package/import evidence only; it does not establish rigid-tool geometry generation, mesh/solver mapping, native profile support, execution permission, or real-model success.

## Report-stage postchecks

After the final report text was complete, only this report was staged (`A  docs/reviews/2026-09-07-p1-b5-rigid-kinematics.md`). The staged checks retain expanded argv, cwd, Python runner, UTC timestamps, staged dirty state, raw output paths, and final production SHA `ce90fbd5046d97fb24bffe1ea1d752ba2fa0b570` in their metadata records.

| Record | Result |
|---|---|
| `P1-B5-report-diff-check-final-02` | `git diff --cached --check`; exit 0 |
| `P1-B5-report-scanner-final-02` | `scripts/scan_cae_data.py --root .`; PASS with 54 files checked, 0 diagnostics, exit 0 |

## Unverified items and next task

- Generated primitive geometry provenance, nonempty/contact-surface resolution, mesh independence, FEBio mapping, native six-DOF semantics, solver stability, and full `CaseSpec` binding remain unimplemented or unverified.
- Lifecycle/CAS and atomic persistence, registry/profile resolution, execution authority, FBS/Studio compatibility, real CAD/mesh inputs, authorized `02_CAE` data, BottomFrame, and all real-model E2E remain unperformed.
- No native profile readiness, schema freeze, readiness, authorized execution, or product completion claim is made by this slice.

Next task: the PM should independently whole-review final production candidate `ce90fbd5046d97fb24bffe1ea1d752ba2fa0b570`, integrate only a whole `ACCEPT` into `V2`, rerun fresh post-integration gates, and preserve the synthetic/local versus real E2E boundary. This worker will not push or integrate.
