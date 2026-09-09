# P2 controlled curved tools and native-session ownership

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

## Executive summary

The scoped H2/M2 candidate supports synthetic sphere and cylinder mesh consumers
with explicit approximation criteria and preserves occupied native sessions in
API-double tests. It is ready for independent scoped review, not whole P2
acceptance. Registered-provider composition and native qualification remain open.

Start: `7db4618e824a5b3790523593e481cd95135506a4`.
Production and clean gate SHA: `221f2f168f9be7ad31070e888a355478e1be4afd`.
The report-only final SHA is supplied in the handoff.
REMOTE_CONFIGURED: https://github.com/A6721jpn/cae-harness.git; integration `V2`.
Branch: `codex/p2-geometry-remediation`. No integration or push performed.

## Decision context

Review must determine whether the private criteria consumer, controlled affine
Tet10 approximation, and exclusive native-session lifetime are suitable for the
next registered-composition step. A matching digest alone is not authority.

The private constructor injection is
`resolve_mesh_quality(NumericalProfileRef) -> ApproximationCriteria | None`.
The frozen criteria contain the exact profile reference, positive length error
limit, immutable supported kinds, algorithm/version ID, evidence scope, and
positive element cap. Missing, foreign, stale or unsupported results fail.
The composition layer supplies the trusted provider; this implementation does
not register profiles or create a public domain record, codec or registry.

## Scope and conditions

This milestone covers tool generation through `mesh(revision)`, with existing
source resolution, whole-body contact selections, ownership and canonical Tet10
mapping. It retains the previous quadratic-mapping positivity check. Primitive
local coordinates are in metres; authoritative placement is subsequently applied.
There is no physical material or contact-performance validation in this evidence.

The deterministic examples use radius 2 mm, cylinder height 4 mm, maximum
corner-edge size 5 mm, five permitted refinements, a 20,000-element cap and a
10 s enclosing mesh-call budget. Those are explicit synthetic inputs, not defaults
or production allowances. Native CAD mapping and general directed curved contact
placement are outside the demonstrated class.

## Method and evidence ledger

Repository source and captured command outputs are the primary evidence. No
external numerical benchmark or native API execution is represented as passed.

| ID | Type | Claim | Applicability and source |
|---|---|---|---|
| E1 | Published | Behavioral RED and clean scoped gates | Local command records listed below |
| E2 | Calculation | Whole-surface radial deviation bounds | `approximation.py`, derivation below |
| E3 | Assumption | Explicit synthetic profile and dimensions | `test_curved_support.py` and observation runner |
| E4 | Calculation | Mesh changes under tighter criteria | Four clean synthetic consumer observations |
| E5 | Inference | Candidate merits scoped review | E1-E4; not registered/native acceptance |

## Input data and implementation manifest

Changed paths relative to the start:

```text
src/febio_cae/adapters/geometry/adapter.py
src/febio_cae/adapters/geometry/gmsh_occ.py
src/febio_cae/adapters/meshing/approximation.py
src/febio_cae/adapters/meshing/primitives.py
tests/component/geometry/test_curved_support.py
tests/component/geometry/test_gmsh_session.py
docs/reviews/2026-09-08-p2-curved-support-intermediate.md
```

Test commits, preceding the production commit:

```text
e10c1903e83b74fed5c5e3c2b13464bf5a6aeedb initial behavioral tests
342039fefe49568232b6d8273c1a3fcbdfa50aaa reverse correspondence/budget/cleanup
2be526e853da24b9a2cc1b0b8e99df9c3d6773a3 fixed-z cylinder test correspondence
cc1f008aec9d1f1d0213f4fdcb3ca2d8f29fa789 independent positive-volume checks
```

## Calculation method

Sphere: radially subdivide an octahedral boundary, projecting new boundary
vertices to radius R. Cone each triangle to the center. Its plane distance d
from the center bounds the radius of every point in that triangle below by d;
all points are inside the sphere. Spherical triangles continue to tile all
directions. Radial correspondence therefore bounds both surface directions by
`delta = R - min(d)` over all triangles, including contact-facing facets.

Cylinder: an inscribed n-sided prism has the two-direction radial bound
`delta = R * (1 - cos(pi/n))`. Holding z fixed gives the same bound on its side
and on the missing end-cap annulus. For R = 2 mm and n = 8, delta is about
0.15224 mm. At n = 16 it is about 0.03843 mm. This is a length-to-length
comparison against the explicit allowance, not a mesh-size proxy.

The implementation adds 64 ulps of R and rounds the result upward once as a
floating arithmetic margin. It is not an interval-arithmetic proof for arbitrary
coordinate scales. Nonrepresentable/degenerate numerical cases fail quality.

After boundary criteria pass, conforming affine tetrahedron subdivision reduces
the actual maximum corner-edge length until it meets global_size. Tet10 midsides
are exact edge averages in local arithmetic; they are not projected to the
analytic surface. This preserves the represented planar boundary while reducing
volume element size. Every mapped element still passes the existing whole-element
quadratic positivity certificate. Geometry and volume refinements consume the
same max_refinements allocation; predicted element counts are checked before
growth. A single monotonic deadline starts at mesh entry and is checked through
generation/mapping and before artifact return. One CPU is used.

## Results and interpretation

Clean synthetic observations at the production SHA:

| Primitive | Error allowance (mm) | Recorded upper bound (mm) | Tool tetrahedra | Geometry refinements |
|---|---:|---:|---:|---:|
| Sphere | 0.7 | 0.36701 | 32 | 1 |
| Sphere | 0.1 | 0.03054 | 512 | 3 |
| Cylinder | 0.7 | 0.15224 | 24 | 0 |
| Cylinder | 0.1 | 0.03843 | 48 | 1 |

All four bounds meet their explicit allowance. Tool approximation criteria,
measured bound, maximum edge, refinement counts and element count are recorded
in the `tool-boundary-deviation-upper-bound` quality reason. Tool recipe identity
binds these inputs/results and is included in the combined mesh recipe. The
separate general CAD surface-approximation record remains UNVERIFIED.

Independent tests check sampled mesh-to-analytic distances, reverse analytic-to-
mesh ray correspondence (fixed-z for cylinders), closed oriented boundary edges,
complete tool selection coverage, positive tetrahedron volumes within analytic
volume bounds, canonical midside positions, changed meshes under stricter error
and size criteria, and bounded refusal cases. Sampling is a regression check;
it does not replace the analytical bound.

M2: an initialized Gmsh session is rejected before options, models, clear or
finalize are touched. Missing ownership-query support also fails closed. Only
an operation that successfully initialized its own session can clear/finalize.
Doubles verify occupied-state preservation, ordinary operation failure cleanup,
and cleanup when option configuration raises during context entry.

## Recommendations

Independently review this exact candidate before registered-provider composition.
The explicit element cap limits memory growth at the cost of truthful refusal;
it must be supplied by trusted configuration. The next integration step belongs
to the registered-provider owner and must bind actual profile/evidence authority,
not substitute the deterministic test provider.

## Verification chronology and commands

Child Python for every wrapped record:
`C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe`.
Cwd: `C:/Users/backo/.codex/worktrees/2ef6/CAE-harness`.
Ignored `.local/coordination/runs/<record>/` retains absolute argv, UTC, HEAD,
dirty-before/after, exit code and raw stdout/stderr. Commands below show child
arguments after that Python executable.

| Record | Exact child arguments | Count/result | Exit |
|---|---|---|---:|
| P2-curved-red-01 | `-m pytest tests/component/geometry --basetemp C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-curved-red-01 -q` | 11 failed, 29 passed | 1 |
| P2-curved-red-02 | `-m pytest tests/component/geometry/test_gmsh_session.py --basetemp C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-curved-red-02 -q` | 1 failed, 2 passed | 1 |
| P2-curved-green-01 | `-m pytest tests/component/geometry --basetemp C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-curved-green-01 -q` | 43 passed | 0 |
| P2-curved-format-02 | `-m ruff format --check src/febio_cae/adapters/geometry src/febio_cae/adapters/meshing tests/component/geometry` | 17 files formatted | 0 |
| P2-curved-lint-01 | `-m ruff check src/febio_cae/adapters/geometry src/febio_cae/adapters/meshing tests/component/geometry` | all checks passed | 0 |
| P2-curved-types-01 | `-m mypy src/febio_cae/adapters/geometry src/febio_cae/adapters/meshing tests/component/geometry` | 17 source files | 0 |
| P2-curved-observations-02 | `.local/coordination/P2-curved-observations.py` | 4 synthetic consumer observations | 0 |

RED01 was clean e10c190 before any production change: existing curved refusal
prevented the positive consumers and explicit criteria diagnostics; occupied
session did not raise. No missing-import/constructor/collection errors supplied
that RED. RED02 was at 342039f with in-progress production changes; it genuinely
failed because context-entry BackendError skipped owned cleanup, and is not
described as a clean run. All final green/static/observation records were clean
221f2f1 before and after. The report is the only subsequent tracked addition.

Development checks included corrected mypy/import/lint issues. A reverse cylinder
test initially used a 3-D radial correspondence that unnecessarily moved z; the
test was corrected to the actual fixed-z bound. Format01 returned 1 for mixed
line endings; format normalization and index refresh changed no Git blob, then
clean Format02 passed. Observation01 failed because the helper lacked the src
import path; it is not evidence. No earlier milestone chronology is relabeled.

## Limitations and unresolved work

H2 is addressed only for the private controlled consumer, pending review and real
registered-provider wiring. Criteria evidence scope is trusted configuration,
not a qualification decision made here. There is no SUPPORTED native registration.
The geometric bound concerns the represented local primitive; finite-precision
placement and extreme scales remain numerical applicability limits.

The deadline is cooperative within one mesh call: it cannot interrupt a blocked
external source/provider/native call, and does not establish cross-call case
accounting. max_attempts is not repurposed as a mesh-refinement count. Hard native
process budgets and enclosing application accounting remain integration work.
The native session policy requires exclusive use; no real Gmsh run occurred.

Standalone placed-selection context, registered composition, verified cache reuse,
GM-01/02/03 native qualification, full required suite/scanner/build/installed smoke,
whole independent acceptance, FEBio/FBS/Studio and real-model/BottomFrame E2E are
still open. Previous whole-gate evidence is not transferred to this candidate.

## References

- [V2 design authority](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md).
- [V2 implementation and verification plan](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md).
- [Private approximation implementation](../../src/febio_cae/adapters/meshing/approximation.py).
- [Connected curved regression tests](../../tests/component/geometry/test_curved_support.py).
- [Native-session API-double tests](../../tests/component/geometry/test_gmsh_session.py).
