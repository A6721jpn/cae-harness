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
| production SHA | `a2551f9a566960ac35943548b36215b66331557f` |
| final code candidate | `a2551f9a566960ac35943548b36215b66331557f` |
| authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote state | `REMOTE_CONFIGURED` |
| push/integration | not performed by this worker |

The test-only commits add and correct only `tests/unit/contracts/test_support.py` and `tests/unit/contracts/test_rigid.py`. The production commit adds `src/febio_cae/domain/support.py`, `src/febio_cae/domain/rigid.py`, and their explicit exports in the existing `src/febio_cae/domain/__init__.py`. The candidate tree was clean after the production gates.

## Test-first RED / GREEN

All captured commands used `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe` (Python 3.12.10). Each basetemp below was preflighted as absent before use.

### Availability RED

At clean test-only SHA `fd740cc0913b9e4da072acdf920177438d879cca`, both production modules were absent. The exact pytest command was:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_support.py tests/unit/contracts/test_rigid.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B3-red-01 -q
```

It collected 39 tests, produced 2 API-availability assertion failures and 37 semantic skips, and exited `1`. There were no collection or environment failures. This is availability RED evidence.

The initial implementation run exposed one invalid test fixture digest (`"z"` is not lowercase hexadecimal). Test-only SHA `96627014997fe8fa139b7d7af09a8e6ac99b1cf7` corrected that fixture without changing production behavior.

### Focused GREEN

At clean production SHA `a2551f9a566960ac35943548b36215b66331557f`, the exact focused command was:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_support.py tests/unit/contracts/test_rigid.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B3-green-02 -q
```

The result was 39 passed, exit `0`.

## Implemented contracts

- `SupportId` is an opaque support identifier. `SupportComponent` accepts only explicit `fixed` or `free` intent and an `EvidenceRef` targeting one of `support.x`, `support.y`, or `support.z`.
- `SolidSupport` requires a `support_surface` `SelectionRef`, explicit support frame, all three translational components, and exact component-to-evidence binding. If a transform is omitted, the selection and support frames must match. If supplied, the `RigidTransform` must map the selection frame to the support frame. No rigid-mode autofix or hidden constraint is synthesized; all-free intent remains explicit.
- `SupportSet` copies and sorts supports by semantic `SupportId`, rejects duplicate IDs, and serializes deterministically. The public alias `SupportCollection` refers to the same contract.
- `RigidPrimitive` supports only `sphere`, `cylinder`, and `box`, with exact dimension sets and explicit conventions: sphere center at local origin, cylinder centered on local z axis, and box centered on local x/y/z axes. Every dimension is a positive length `Quantity`; constructor-time `to_si()` validation preserves the existing finite-range and underflow policy without clamping or rounding.
- Rigid placement requires a typed `BodyId`, `local_frame`, and `RigidTransform` whose source frame matches the local frame. Translation components are validated through `Quantity.to_si()` at construction, including explicit identity placement. Model, placement, and each dimension retain field-bound `EvidenceRef` values. Caller mappings are copied into immutable mappings.
- `to_dict` projects quantities in SI units and `to_bytes` delegates to the existing canonical serializer. No public `from_dict` boundary was added because the nested selection and spatial contracts do not yet provide a shared restore boundary.

## Final local gates

All gates below ran at production SHA `a2551f9a566960ac35943548b36215b66331557f` with an empty working tree before and after each product check.

| Record | Exact command | Result |
|---|---|---|
| `P1-B3-gate-pytest-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-B3-full-01 -q` | 214 passed, exit 0 |
| `P1-B3-gate-format-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .` | 43 files already formatted, exit 0 |
| `P1-B3-gate-lint-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .` | all checks passed, exit 0 |
| `P1-B3-gate-mypy-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests` | no issues in 27 source files, exit 0 |
| `P1-B3-gate-scanner-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` | PASS; 45 files checked, 0 diagnostics, exit 0 |
| `P1-B3-gate-build-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build` | sdist and wheel built, exit 0 |

## Wheel and installed smoke

```text
Wheel: C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 7761239dd2d3e1a8d365e6f580fc7d9170922d980ac197585e04573f027b7c68
Size: 30999 bytes
```

The fresh installation root was `.local/verification/P1-B3-installed-smoke-01`; the wheel was installed into a new Python 3.12 virtual environment with `--no-deps`.

| Check | Result |
|---|---|
| venv creation | exit 0 |
| wheel installation | `Successfully installed febio-cae-0.1.0`, exit 0 |
| `febio-cae --version` | `febio-cae 0.1.0`, exit 0 |
| isolated domain imports | `RigidPrimitive SolidSupport`, exit 0 |

Installed smoke is package/import evidence only; it does not establish solver, FBS, Studio, input-generation, or real-model success.

## Unverified items and next task

- Lifecycle/CAS and atomic persistence, case aggregation, contact, mesh/solver policies, native FEBio/FBS/Studio compatibility, registry resolution, and physical adequacy remain unimplemented or unverified in this slice.
- Real CAD/mesh inputs, authorized `02_CAE` data, BottomFrame, solver execution, official FBS evidence, and real-model E2E remain unperformed.
- Independent exact-commit review, integration into `V2`, and push remain outside this worker handoff.

Next task: the PM should review this exact clean commit sequence, integrate only the reviewed candidate into `V2`, and preserve the synthetic/local versus real E2E verification boundary.
