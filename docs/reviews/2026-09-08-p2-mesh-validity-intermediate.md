# P2 mesh validity intermediate repair

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

This intermediate candidate remains unaccepted. H1, H7 and M1 have scoped repairs;
H2 now fails explicitly for unsupported curved-tool quality but sphere/cylinder
generation remains required and open. No native execution occurred.

Starting candidate: `eaaf046e5617c7ffb366bcc84dbdf4c7f967873e`.
Accepted integration base remains `c23dc59d4dae4810acd51fe2156b7d190d645acb`.
REMOTE_CONFIGURED: authorized repository https://github.com/A6721jpn/cae-harness.git,
integration branch V2. This worker neither integrated nor pushed.

Tests: `93066eb37110e1d97e34bda45bbe27bee2bdd314`.
Fixture correction: `1560caa3adc73d78ddf8ad61aa2d085a90761016`.
Production: `4845b49bf6415d0c4521847392a68c461c3ccc4a`.
Clean scoped gate candidate: `11e5b56f2e32b138e41f04dfa7aedeeb1804ccda`
(test command formatting only after production).

Changed implementation files: geometry/adapter.py, geometry/gmsh_occ.py,
new geometry/quadratic_quality.py, and meshing/primitives.py under
src/febio_cae/adapters/. Tests changed: tests/component/geometry/conftest.py,
test_adapter.py and new test_mesh_validity.py. This report is the only other path.

## Behavior and limits

The box uses all six tetrahedra around its body diagonal. The consumer regression
checks positive signed total volume against requested dimensions, coverage area
of all six planar sides, no exposed internal cuts, and strict MeshArtifact codec
round-trip. Meshing-first import in a fresh isolated Python process now works;
primitive imports in the adapter are deferred until their methods are called.

Both adapter projection and the native backend's Tet10 extraction apply the same
quadratic quality rule. For positive affine corner Jacobian A, the actual quadratic
Jacobian is J(x)=A(I+D(x)). D is affine in reference coordinates. Its infinity norm
is bounded everywhere by the maximum norm at the four reference vertices. A bound
strictly below one guarantees nonsingularity of I+tD for every t in [0,1], so its
determinant cannot change sign. The implementation requires a margin of 1e-10 from
one and finite arithmetic. This is a sufficient whole-reference-element rule,
not a sample of determinant signs or a necessary criterion. It can refuse valid
strongly curved elements; numerical conditioning and floating-point arithmetic
remain limitations, and no interval-arithmetic proof or native qualification is
claimed. Canonical ordering and corner volume are checked separately.

The folded-edge regression keeps positive corners while moving edge 0-1's node
to (-0.04,0,0) on a 0.01 m reference tetrahedron. It now raises QUALITY with a
quadratic-mapping reason. The pre-existing synthetic backend fixture mislabeled
canonical connectivity as backend connectivity; its final two edge IDs are now
corrected. The shared positive fixture uses a box. Sphere/cylinder requests raise
UNSUPPORTED_CAPABILITY with a curved-quality diagnostic. Controlled curved
generation, analytic/bidirectional deviation and contact-facing checks remain H2
work; returning an error does not close that feature.

## Captured evidence

All records are under ignored .local/coordination/runs/. The wrapper captures
actual argv, cwd, Git metadata cwd, HEAD, dirty state, UTC, exits and raw streams.
Python executable for every recorded child below:
`C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe`.
Normal cwd: `C:/Users/backo/.codex/worktrees/2ef6/CAE-harness`.

- P2-validity-red-01: `-m pytest tests/component/geometry --basetemp
  .local/verification/P2-validity-red-01 -q`, clean test commit 93066eb;
  5 failed, 16 passed, exit 1. The box test initially encountered the old sphere
  fixture's missing dimensions; that failure is not evidence for H1.
- P2-validity-baseline-red-02: corrected tests at 1560caa replayed against a fresh
  Git archive of starting eaaf046 under .local/verification/validity-baseline.
  Child cwd is that archive; `-m pytest tests/component/geometry --basetemp
  C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-validity-baseline-red-02 -q`.
  5 failed, 16 passed, exit 1: box volume, quadratic fold, first import, sphere
  and cylinder rejection. Wrapper HEAD describes the owning checkout, whose
  implementation was dirty; the executed archive holds the original product and
  corrected tests. This replay occurred after implementation began and is not
  relabeled as a clean pre-implementation run.
- P2-validity-green-01: `-m pytest tests/component/geometry --basetemp
  C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-validity-green-01 -q`;
  clean 11e5b56 before/after, 21 passed, exit 0.
- P2-validity-format-02: `-m ruff format --check
  src/febio_cae/adapters/geometry src/febio_cae/adapters/meshing tests/component/geometry`;
  12 formatted files, exit 0. The earlier -01 returned 1 for mixed line endings
  in the edited test; normalization changed no Git blob.
- P2-validity-lint-01: `-m ruff check src/febio_cae/adapters/geometry
  src/febio_cae/adapters/meshing tests/component/geometry`; all checks passed, exit 0.
- P2-validity-types-01: `-m mypy src/febio_cae/adapters/geometry
  src/febio_cae/adapters/meshing tests/component/geometry`; 12 source files, exit 0.

All GREEN/static records above identify clean 11e5b56. Development runs and the
earlier lint failure are not clean gate evidence. The initial development run
failed because the old fixture still requested a sphere and its edge order was
wrong; corrected development execution passed 21 tests before the clean run.

The independent review's M3 chronology correction supersedes the old report's
claim that its historical focused/full/static runs all occurred at clean 1f2bc1f.
Those old outputs must not be used as clean-final evidence. The final coherent
repair will correct that report and run the full required gate set once.

## Remaining work

H2 controlled curved-tool support; H3/H4 selection coverage and frame transforms;
H5/H6 arrangement identity and valid unique contact placement; H8 unit agreement;
M2 native session ownership; cache implementation, GM-01/02/03, native mapping,
CAD approximation, installed full integration, FEBio/FBS/Studio and real-model/
BottomFrame remain open. Full suite/build/install were deliberately deferred
under the finite intermediate-slice work order. Next: independent scoped review
and continued coherent repairs by the same geometry owner before whole acceptance.
