# P2 connected geometry binding intermediate repair

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

This is a scoped intermediate repair for H3/H4/H5/H6/H8, pending independent
review. Whole P2 acceptance and native qualification remain open.

Start: `f247e3132a25bb173537d203fdb857b4b9302aef`.
Accepted V2 integration base remains `c23dc59d4dae4810acd51fe2156b7d190d645acb`.
REMOTE_CONFIGURED: https://github.com/A6721jpn/cae-harness.git, integration V2.
Worker branch: codex/p2-geometry-remediation. No integration or push performed.

Test commit: `e7c1a449b9f4f952dd868496f1577ba170730cb3`.
Typed test correction: `bbc7aee1f955236fdedcaca6d65189c8885f7a10`.
Explicit triangular fixture/record check: `bf9b075b03f40d06f40b54eed2dfea6e5334e166`.
Production: `294cea130c119e6c5e0a4958d3d6b7634fe50ed1`.
Final diagnostic assertions and clean gate candidate:
`25c1cbd21354731a551bff0a6bdd4ddc256f9d30`.
The report-only final SHA is delivered separately in the handoff.

Changed paths relative to start:

```text
src/febio_cae/adapters/geometry/adapter.py
src/febio_cae/adapters/geometry/backend.py
src/febio_cae/adapters/geometry/planar_gap.py
src/febio_cae/adapters/meshing/primitives.py
tests/component/geometry/conftest.py
tests/component/geometry/test_connected_binding.py
docs/reviews/2026-09-08-p2-connected-binding-intermediate.md
```

## Connected behavior

H3: every resolved CAD face must have nonempty one-adjacent-element coverage
through the explicit backend source_face_id relation within its body. Prefix
matching no longer establishes coverage. Generated primitive boundary facets now
carry the same explicit relation. Mapping validates body ownership before sets.

H4: source inspection measurements and complete boundary points receive the
same authoritative GeometryIntent transform as mesh nodes. Source and geometry
digests remain unchanged. Native source-frame disagreement is rejected. The
consumer test uses consistent native inspection/mesh frames with nonidentity
rotation and translation, verifies actual corner positions and complete sets,
and round-trips the artifact through the strict common codec.

H8: a case/source STEP unit disagreement raises INTEGRITY before backend mesh
generation. Already-SI coordinates are not rescaled to conceal the disagreement.

H5: mesh() computes initial placement, applies it to tool nodes and tool selection
measurements, and binds both the arrangement input and applied result into recipe
identity. An initial-contact-placement record contains the full canonical result
including transform, current/requested gap, direction, selected face IDs and delta.
The regression verifies zero/positive gap geometry, changed recipe identity and
the recorded requested gap.

H6 supported class: a single selected triangle from each closed, consistently
oriented triangular body boundary; both selected planes perpendicular to the
explicit direction and facing each other; positive-area overlap in projection;
all part/tool points on the required sides of their support planes. Convex
triangle overlap is checked by the separating-axis theorem, not sampling.
Translation along the direction preserves this overlap. Opposite enclosing
half-spaces establish noninterference of the final placement for nonnegative gaps.
Negative requested overlap, sloped planes, absent projected overlap, ambiguous
multiple selections, missing complete triangular topology or invalid closure
are rejected. Existing AsPlaced behavior retains explicit placement.

The reserved backend attribute planar-triangle-v1 asserts that boundary_points_si
are the entire oriented affine triangle, not samples of a curved face. It is
required on every boundary face and documented in BackendFace. Generated box
faces and the synthetic fixture provide it; current native CAD inspection does
not, so this gap path is explicitly unsupported there. No broad CAD contact-
placement capability is claimed. Geometry comparison tolerance is numerical
(max(1e-12 m, extent*1e-12)); it is not a physical approximation allowance.
Floating-point and represented-boundary accuracy remain limits.

## Evidence chronology and scoped gates

Each wrapper record retains actual absolute child Python, cwd, HEAD, dirty state,
UTC, exit and raw stdout/stderr under ignored .local/coordination/runs/.
Child Python: `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe`.
Cwd: `C:/Users/backo/.codex/worktrees/2ef6/CAE-harness`.

P2-binding-red-01 at clean e7c1a44: 7 failed / 21 passed, exit 1. Five failures
were malformed typed-test constructors and are not behavioral RED evidence.
P2-binding-red-02 at clean bbc7aee, before production changes: 7 behavioral
failures / 21 passed, exit 1. Coverage and unit conflict did not raise;
the transformed selection was empty; tool top stayed at 0.022 m instead of
the requested contact plane; sloped/overlap/no-footprint requests did not raise.
Both commands were `-m pytest tests/component/geometry --basetemp
C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-binding-red-01 -q`
and the same argv with final basetemp suffix P2-binding-red-02, respectively.

All four final records below ran at clean 25c1cbd before/after:

| Record | Child arguments after absolute Python | Result | Exit |
|---|---|---|---:|
| P2-binding-green-final | `-m pytest tests/component/geometry --basetemp C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-binding-green-02 -q` | 28 passed | 0 |
| P2-binding-format-final | `-m ruff format --check src/febio_cae/adapters/geometry src/febio_cae/adapters/meshing tests/component/geometry` | 14 files formatted | 0 |
| P2-binding-lint-final | `-m ruff check src/febio_cae/adapters/geometry src/febio_cae/adapters/meshing tests/component/geometry` | all checks passed | 0 |
| P2-binding-types-final | `-m mypy src/febio_cae/adapters/geometry src/febio_cae/adapters/meshing tests/component/geometry` | 14 source files | 0 |

Earlier clean production GREEN-01 passed 28 tests; final assertions pin the
specific unsupported-geometry diagnostic and category. Development mypy/lint
errors were corrected before the clean gates. Format-01 returned 1 for mixed
line endings; normalization changed no Git blob. Format-02 saw a dirty index
stat before refresh and is not final evidence; format-03 and final capture are
clean. No historical H1 RED or old M3 gate chronology is relabeled. The first
handoff's old clean-1f2 gate claims remain superseded by the independent review
and must be corrected in the final coherent report.

## H2 approximation profile need and remaining interfaces

H2 stays OPEN: sphere/cylinder requests currently stop with unsupported quality.
MeshPolicy supplies global_size, max_refinements and a NumericalProfileRef with
purpose mesh_quality and record_digest. None supplies a surface-error allowance.
CompatibilityRegistryPort resolves a solver/reader CompatibilityProfile, not
the referenced numerical approximation criteria.

Proposed next private C constructor injection:
`resolve_mesh_quality(ref: NumericalProfileRef) -> QualifiedApproximationCriteria`.
The owner-provided immutable criteria must bind the exact ref/digest, explicit
positive max_boundary_deviation Quantity, supported primitive kinds, qualified
algorithm/version and supporting evidence. C checks those bindings and consumes
global_size plus the explicit error bound and max_refinements within case budget;
it records measured analytic/bidirectional error including contact-facing facets.
Sphere/cylinder refinement must stop if either size/error target cannot be met.
A supplies registered resolution; C owns its narrow consumer protocol. PM must
decide the resolver ownership/record contract before implementation; no common
contract or default physical tolerance was added here.

A separate frozen-port limit: GeometrySelectionRequest has source and selection
but no GeometryIntent placement. Standalone resolve_selection therefore resolves
in the native report frame; transformed resolution is supplied here by mesh()
using its authoritative revision. If A requires pre-mesh placed resolution through
GeometryPort, it needs an explicitly bound revision/placement context rather
than inferring one from a frame name. This is reported for PM coordination, not
silently added to shared contracts.

H2, native session M2, verified cache reuse, GM-01/02/03, native approximation,
full registered application integration, FEBio/FBS/Studio and all real-model/
BottomFrame E2E remain open. The prior quadratic quality rule is unchanged.
Full suite, scanner/build/install and whole independent acceptance are deferred
to the final coherent candidate as directed. No native run or real CAE access
occurred. Next: scoped review and resolver decision, then remaining repairs by
the same geometry owner.
