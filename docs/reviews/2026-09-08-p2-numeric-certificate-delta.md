# P2 H7 exact-arithmetic certificate delta

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

Scoped candidate for independent review; H7 and whole P2 acceptance are not closed.
Base: `535688ab619ceb1b9ddc68b9e93d1d08cfd7e363`.
Code and clean gates: `c962ae9bc2ab7e723f4b7174a20525f2643c25cc`.
The final report-only SHA is supplied in the handoff.
REMOTE_CONFIGURED: https://github.com/A6721jpn/cae-harness.git, integration V2.
No integration, push, native execution, dependency or common-interface change.

## Delta and manifest

The supplied binary coordinates are converted losslessly to integers with a
common power-of-two denominator. Positive corner orientation is established by
an exact integer determinant d. For each reference vertex, the certificate
compares integer adjugate-times-Jacobian-perturbation row sums directly against
d times the exact threshold `(10**10 - 1)/10**10`. There is no floating inverse,
determinant-sign assumption or unbounded rounding step in this decision. The
existing sufficient whole-element norm theorem and conservative threshold remain.

The helper returns a positive corner volume rounded from the exact rational
value only after certification; overflow or underflow to zero is refused. Both
backend extraction and adapter projection use the shared certificate. Adapter
quality values now use its returned volume, not a cancellation-prone determinant.
The certificate concerns the actual represented coordinates, not geometric intent.

```text
src/febio_cae/adapters/geometry/quadratic_quality.py
src/febio_cae/adapters/geometry/adapter.py
src/febio_cae/adapters/geometry/gmsh_occ.py
tests/component/geometry/test_numeric_certificate.py
docs/reviews/2026-09-08-p2-numeric-certificate-delta.md
```

## Evidence

Clean pre-correction test commit `b770f8be1329c6f26f2e0f9cd56b7eb7e913b5de`
produced genuine RED: 5 failed, 53 passed, exit 1. The exact-coplanar witness
and two binary-scaled versions were incorrectly accepted by the helper; the
native-array double and connected consumer also incorrectly accepted it.
An independent Fraction determinant is zero for each witness. There were no
collection, fixture-constructor or environment failures in this RED.

Supplemental tests precede production in commit e90cc2d. They compare the norm
decision with independent Fraction/Gauss-Jordan calculations using full Tet10
shape derivatives, including an ill-conditioned shear and near-threshold curves.
Other neighbors cover translated non-diagonal affine/mild-curved inputs scaled
by 2**-100 and 2**100, an exactly affine near-degenerate positive, ordinary
flat/inverted/folded cases, and unrepresentable output volume. The complete scoped
run also re-executes the actual controlled sphere/cylinder consumers and session
preservation tests. Synthetic/API-double evidence is not native qualification.

Child executable: `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe`.
Cwd: `C:/Users/backo/.codex/worktrees/2ef6/CAE-harness`.
Ignored `.local/coordination/runs/` records preserve actual argv, HEAD, clean/dirty
state, UTC, raw streams and exit. All final records below are clean c962ae9 before
and after; subsequent tracked change is this delta only.

| Record | Child arguments | Result | Exit |
|---|---|---|---:|
| P2-numeric-red-01 | `-m pytest tests/component/geometry --basetemp C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-numeric-red-01 -q` | 5 failed, 53 passed | 1 |
| P2-numeric-green-01 | `-m pytest tests/component/geometry --basetemp C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-numeric-green-01 -q` | 66 passed | 0 |
| P2-numeric-format-01 | `-m ruff format --check src/febio_cae/adapters/geometry src/febio_cae/adapters/meshing tests/component/geometry` | 18 formatted files | 0 |
| P2-numeric-lint-01 | `-m ruff check src/febio_cae/adapters/geometry src/febio_cae/adapters/meshing tests/component/geometry` | all passed | 0 |
| P2-numeric-types-01 | `-m mypy src/febio_cae/adapters/geometry src/febio_cae/adapters/meshing tests/component/geometry` | 18 source files | 0 |

Two development test-oracle typing errors were corrected before clean gates;
no failed check is presented as passing. Earlier milestone chronology is unchanged.
Next: independent exact-candidate review. Strongly curved valid elements may still
be conservatively refused. Registered criteria composition, placed-selection common
extension, native/cache/GM qualification, full required gates and real E2E remain
open; previous whole-suite/build/install results are not transferred to this SHA.
