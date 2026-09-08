# P2 C2-M1 session-admission correction

Ready for exact delta review, not whole P2 acceptance. Base
`e4c46700a638992fd715401d6ef1e7fbf7788ee0`; production and fresh-gate source
`893fa4c695d6c985fc2b97e1eb11a5f96c2da3c2`. Final report-bearing SHA is delivered
separately. REMOTE_CONFIGURED: https://github.com/A6721jpn/cae-harness.git, V2.

Changed paths: `src/febio_cae/adapters/geometry/gmsh_occ.py`,
`tests/component/geometry/test_gmsh_session.py`, and this report. No other product
or common file changed; prior evidence and historical M3 correction are preserved.

One process-wide non-reentrant lock is acquired without waiting before the native
state query, and retained through initialization, body, clear/finalize and local
cleanup. Busy/re-entry refuses without mutation. Externally initialized state
still refuses without native mutation. Initialization attempts following an
uninitialized query are owned for cleanup, including partial initialization.
Nested finally blocks attempt finalization even after clear failure and release
admission even if cleanup raises. Noncooperating external native users and real
Gmsh thread affinity remain outside this adapter-only exclusion guarantee.

Clean test commit `41b90cfa25bbd5f7bca8805fc5fda4fbef705ee2` produced RED before
production: 4 failed, 4 passed, exit 1. An event-held first state query allowed a
second session to enter; re-entry failed the busy contract, partial initialize
leaked initialized state, and clear failure was swallowed. Thread waits/joins
are bounded at 3 s. Supplemental test commit 7b99da8 checks ownership through
cleanup. The final focused set includes sequential, externally occupied, entry/
body/exit failure, nested and different-wrapper neighbors: 9 passed, exit 0.

## Fresh gates and artifact

Commands use `C:/Users/backo/AppData/Local/Programs/Python/Python312/python.exe`
with cwd `C:/Users/backo/.codex/worktrees/2ef6/CAE-harness`. Arguments follow that
executable below. All final commands ran at clean 893fa4c before and after.

| Record | Child arguments | Result | Exit |
|---|---|---|---:|
| P2-admission-red-01 | `-m pytest tests/component/geometry/test_gmsh_session.py --basetemp C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-admission-red-01 -q` | 4 failed, 4 passed | 1 |
| P2-admission-green-01 | `-m pytest tests/component/geometry/test_gmsh_session.py --basetemp C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-admission-green-01 -q` | 9 passed | 0 |
| P2-admission-full-01 | `-m pytest --basetemp C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-admission-full-01 -q` | 1,120 passed, 22.08 s | 0 |
| P2-admission-format-01 | `-m ruff format --check .` | 122 formatted files | 0 |
| P2-admission-lint-01 | `-m ruff check .` | all passed | 0 |
| P2-admission-types-01 | `-m mypy src tests` | 85 source files | 0 |
| P2-admission-scanner-01 | `scripts/scan_cae_data.py --root .` | 125 checked, 0 diagnostics | 0 |
| P2-admission-build-01 | `-m build --outdir C:/Users/backo/.codex/worktrees/2ef6/CAE-harness/.local/verification/P2-admission-dist-01` | sdist and wheel | 0 |

Stable wheel: `.local/verification/P2-admission-dist-01/febio_cae-0.1.0-py3-none-any.whl`,
137881 bytes, SHA256 `d6ee8bcb931daabb0ccba4b0989d55843adfe915b3261876a47b904013389884`.
Fresh external root: `C:/Users/backo/AppData/Local/Temp/cae-harness-P2-admission-installed-01`.
Venv creation and isolated offline exact-wheel install exit 0; actual
`venv/Scripts/febio-cae.exe --version` returns `febio-cae 0.1.0`, exit 0.
Fresh `python.exe -I consumer.py meshing-first` and `geometry-first` both exit 0,
reusing the prior synthetic installed-consumer driver/data without expanding its
probe scope. These are installed box/sphere/cylinder/codec/ownership observations,
not native execution. All product imports originate in the external site-packages.

Authoritative capture/byte manifest:
`.local/verification/P2-admission-handoff-01/execution-manifest.json` with adjacent
capture metadata. It contains expanded argv/cwd/UTC/HEAD/dirty states/exits and
stream/driver/input/wheel/source hashes. The final report-only scanner is captured
separately; the final source differs from the tested/built source only by this report.
One development lint finding in the test exception collector was corrected before
final gates. Old 1,114-test evidence is historical, not this correction's gate.

Next: exact admission-delta review. Cache/provider/placed-request composition,
native GM/runtime qualification, registered application and all real-model/
BottomFrame E2E remain open. No native, cache, dependency, A/B/common change,
integration or push occurred.
