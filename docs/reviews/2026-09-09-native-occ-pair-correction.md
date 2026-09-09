# Fixed public Gmsh / OCCT candidate correction

Base: `9f5d0c1f1f97d92ffa198c7c13e9a24a99e0aff0`.
Formal decision: `a64db9fe69d20586248e70fe85450f5762eb2559`.
Collected RED tests: `c8fcd39f0b4b056a7d0e923e8f9cc6bfadd8d646`.
Product: `a635fb82c738d88cc10309a7d734d65cc8784880`.
Status: code-review candidate; not native geometry or scientific qualification.

The pinned official Gmsh 4.15.2 Windows distribution reports linked OCC 7.8.1.
Public initial inspection and planar preparation previously required 8.0.1 and
therefore refused this distribution before geometry. The two formal authorities
now explicitly adopt 4.15.2 / owned linked OCCT 7.8.1 / AP214 as a candidate for
qualification, before tests and implementation. No OCCT-8-only feature basis was
identified in the examined V2 compatibility evidence.

Only the three public product files change: inspection backend configuration,
measured identity projection and parent validation; preparation configuration,
projection and producer validation; application preparation publication
validation. All eight fixed OCCT values are 7.8.1. The generic adapter parser,
optional-version contract, exact missing/ambiguous/mismatch refusal, AP214,
units, geometry algorithms, resource limits, ownership, stores and CLI remain
unchanged. There is no fallback, caller-selected native version, independent
OCCT substitution, automatic SUPPORTED profile or tolerance change.

## Distinct native evidence preceding this source correction

The first producer remains **FAILED**: one initialized session, child/wrapper
exit 1, unsupported OCC identity before model construction or STEP write,
pending cleanup 0. Finalize returned through finally, but its post-finalize
state query was not reached. No STEP or producer record was created.

A separately released identity-only diagnostic **PASSED**: one initialized
session, child/wrapper exit 0, finalize returned, initialized-after-finalize
false and pending cleanup 0. Raw `General.BuildInfo` has exactly one
`OCC version: 7.8.1`, with `Version: 4.15.2` and build date `20260324`.
Its JSON SHA256 is
`d1206a0cc6de9b8fadb17af2d0a9aec39d56ba89e52803459c762e689c2a249d`.
This confirms a version mismatch, not a parser-format problem. The two sessions
are separate; no public reader, geometry or mesh success follows from them.

| Identity | SHA256 | Evidence scope |
|---|---|---|
| Official Windows Gmsh 4.15.2 wheel | `7b36083bb410fa27c5d0e052929d1a9844a5b09169d66017b72b41aabd49d711` | Pinned distribution bytes, 42235999 bytes |
| gmsh.py | `a56ebe69dc57a3ea15eee191cae4f1881b06784174b2d9df306a98bdd8b06606` | Installed bytes and separately measured runtime module hash |
| gmsh-4.15.dll | `6cac3eefb477265d9fa60bbd869dbbbf7c7ca4cb308c0f8f2b43e91d43de3c1c` | Named installed bytes, not a separately queried loaded-image path |

Raw commands, timestamps, process identities and failure/diagnostic logs remain
in ignored local evidence. Historical reports and the failed producer artifacts
are unchanged. No native operation ran during this source-correction stage.

## Focused RED / GREEN

Existing public success tests assert real, unmocked backend-factory config
before exercising the synthetic path. Existing refusal tests now cover old
8.0.1, another wrong version and missing OCCT identity on both public flows.
Missing required inspection response fields retain integrity rejection; intact
but wrong identity retains unsupported rejection. Source/geometry mismatch and
cleanup assertions remain intact. Generic optional 8.0.1 admission tests are
unchanged and still exercise inspection and mesh operations.

Exact RED command (candidate checkout, Python 3.12.10):

```text
python -m pytest tests/component/application/test_native_inspection.py::test_initial_native_topology_without_spec tests/component/application/test_native_inspection.py::test_unsupported_policy_and_response_bounds tests/component/application/test_planar_preparation.py::test_current_operation_publishes_bound_preparation tests/component/application/test_planar_preparation.py::test_preparation_refuses_source_or_backend_mismatch tests/component/geometry/test_gmsh_preparation.py::test_configured_preparation_precedes_import_and_cleans_up tests/component/geometry/test_gmsh_preparation.py::test_occt_evidence_refused_before_import_and_session_reusable --basetemp .local/v/occr
```

RED collected 10: 4 failed, 6 passed, exit 1. Both real factories still expected
8.0.1, and both public flows accepted the old identity. These were collected
behavioral failures with the original product, not environment/collection errors.
GREEN uses the identical command with `--basetemp .local/v/occg`: 10 collected,
10 passed, exit 0. Raw evidence: `occ-red.log`, `occ-green.log` and paired metadata
in ignored local verification. No additional test matrix or new test file.

## Cheap code gates

| Command | Result | Exit |
|---|---|---:|
| `python -m ruff format --check .` | 236 files formatted after the correction below | 0 |
| `python -m ruff check .` | All checks passed | 0 |
| `python -m mypy src tests` | No issues in 184 source files | 0 |
| `python scripts/scan_cae_data.py --root .` | PASS, 238 files checked, no issues | 0 |

Initial format check exited 1 for mixed line endings in the modified planar test
file (235 formatted, 1 requiring normalization). The targeted command
`python -m ruff format tests/component/application/test_planar_preparation.py`
exited 0. Normalized text was byte-for-byte unchanged, and refreshing the Git
index left no source/test diff against the product commit. Only format check
was repeated; tests, lint, types and boundary results remain applicable. Raw
failure/correction/final format evidence is retained separately, not counted
as an initial pass. No full suite, build or installation ran in this code stage.

## Pending work

Independent exact-code review, mandatory full suite/build/fresh install, and a
separately released new producer/reader remain pending. Preserve the old accepted
wheel before any later build overwrites its filename. New native evidence must
use a newly accepted artifact and fresh scratch, retain fixed 1x2x3 mm geometry,
AP214/unit/corner admission and analytic tolerances, and bind actual STEP bytes
before reader release. Identity evidence alone closes no geometric/scientific,
profile, FB03/FBS, AI02/API, figure, Studio/Computer Use, required actual E2E or
final BottomFrame gate.
