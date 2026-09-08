# P2 C consolidated adapter-source handoff

Ready for combined independent source review, not P2 completion. Fresh required
local gates pass for clean `5b3ea16562349e2e44ecc0654f5decf6df5958d4`.
This handoff adds only this report and a historical M3 chronology correction;
the exact final report-bearing SHA is supplied separately. Production, tests,
private criteria and recipe interfaces are unchanged in this handoff.
REMOTE_CONFIGURED: https://github.com/A6721jpn/cae-harness.git, integration V2.
Base: `c23dc59d4dae4810acd51fe2156b7d190d645acb`. No integration or push.

## Source scope

The lineage contains box closure, meshing-first imports, explicit source-face
coverage, transformed inspection/selection geometry, applied initial placement
with a restricted planar directed-gap rule, source/case unit agreement, controlled
curved primitives, exclusive native-session ownership, and exact binary-coordinate
Tet10 certification. Intermediate evidence/commits are preserved; these are
implementation claims awaiting combined review, not independent acceptance.

Changed source files under `src/febio_cae/adapters/`:

```text
geometry/__init__.py       geometry/adapter.py
geometry/backend.py        geometry/gmsh_occ.py
geometry/planar_gap.py     geometry/quadratic_quality.py
meshing/__init__.py        meshing/approximation.py
meshing/primitives.py
```

Nine geometry test files and six review documents complete the 24-path delta
from the integration base. Exact paths, Git blobs, package-file hashes, source
comparison rules and every executed argv/cwd/HEAD/dirty-state/UTC/exit/stream hash
are in the authoritative ignored execution manifest:

```text
.local/verification/P2-C-whole-handoff-01/execution-manifest.json
```

## Fresh required gates

All commands use
`C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe` and cwd
`C:/Users/backo/.codex/worktrees/2ef6/CAE-harness`. Table arguments follow that
executable. Each record independently captures its child exit; none relies on
the last status of a multi-command shell chain.

| Record | Exact child arguments | Result | Exit |
|---|---|---|---:|
| P2-whole-pytest-01 | `-m pytest --basetemp C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-whole-pytest-01 -q` | 1,114 passed, 22.35 s | 0 |
| P2-whole-format-01 | `-m ruff format --check .` | 122 formatted files | 0 |
| P2-whole-lint-01 | `-m ruff check .` | all checks passed | 0 |
| P2-whole-types-01 | `-m mypy src tests` | 85 source files | 0 |
| P2-whole-scanner-01 | `scripts/scan_cae_data.py --root .` | PASS, 124 checked, 0 diagnostics | 0 |
| P2-whole-build-01 | `-m build --outdir C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-whole-dist-01` | sdist and wheel built | 0 |

These are fresh clean-source results, not reused older full gates. The final
report-only scanner and artifact/source audit are separately captured in the
manifest; earlier package gates apply because the final code/test blobs match.

## Stable wheel and external installed consumers

```text
Wheel: C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-whole-dist-01/febio_cae-0.1.0-py3-none-any.whl
Bytes: 137640
SHA256: 922a4b524e99b0e928cbc6a7e84b9907a2c9a7989429368e089af556c64ddb68
External root: C:/Users/backo/AppData/Local/Temp/cae-harness-P2-whole-installed-01
```

The dedicated build output preserves older wheel evidence. A fresh external
`venv` was created with `python.exe -m venv <external root>/venv`. From the
external cwd with PYTHONPATH/PYTHONHOME unset, its `venv/Scripts/python.exe`
ran `-I -m pip install --no-index --disable-pip-version-check --no-cache-dir
--no-deps <wheel>` (exit 0). Actual `venv/Scripts/febio-cae.exe --version`
returned `febio-cae 0.1.0`, exit 0.

Two fresh processes ran `venv/Scripts/python.exe -I <external root>/consumer.py
meshing-first` and the same command with `geometry-first`; both exited 0.
Each consumed the public adapter for box, sphere and cylinder, checked strict
artifact codec round-trip, disjoint body nodes, whole-tool face coverage and
positive quality. Curved tools met an explicit synthetic 0.1 mm error allowance.
Every loaded febio_cae module originated under that venv's site-packages; no
pytest/test-module or native gmsh import occurred. Inputs were exported synthetic
test data; the external driver used installed classes and its own backend double.
These are six synthetic installed-consumer observations, not six extra pytest
passes and not registered/native integration evidence. Exact expanded commands,
driver/input hashes, origins and outputs are retained in the manifest.

## M3 correction and outstanding capabilities

The [original report](2026-09-08-p2-input-model-geometry-mesh.md) now correctly
attributes its 18-test run to an in-progress pre-production shell chain; its
1,066-test/type/format/lint runs to c742267 plus an uncommitted test initializer;
and scanner/build to the later 1f2bc1f state. Original bare-python argv and shared
format/lint shell status are not relabeled as independent absolute-child captures.
The preserved audit is hashed by the new manifest. No old evidence was rewritten.

Still missing: registered A-provider and placed-request composition; stored-mesh
cache lookup, serialized-byte verification, tamper detection and safe reuse;
native GM-01/02/03 topology/unit/mapping/approximation and runtime identity;
registered full application/FEBio/FBS/XPLT/Studio flow; all required real-model
and BottomFrame E2E. Recipe hashes and the inspection-detail dictionary are not
a mesh-cache capability. The unreviewed A candidate is not consumed. No cache,
common contract, native run or additional feature was added for this handoff.
Next: combined independent C whole-source review, with these gaps explicit.
