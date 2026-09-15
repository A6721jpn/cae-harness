# Fixed public Gmsh / OCCT candidate correction

Base: `9f5d0c1f1f97d92ffa198c7c13e9a24a99e0aff0`.
Formal decision: `a64db9fe69d20586248e70fe85450f5762eb2559`.
Collected RED tests: `c8fcd39f0b4b056a7d0e923e8f9cc6bfadd8d646`.
Product: `a635fb82c738d88cc10309a7d734d65cc8784880`.
Code accepted: f1b04b8fc3ea59f54e5355c07d58243baa19983f.
Status: mandatory code/build/install gates passed; new producer failed serialized
point admission; reader unexecuted; not native or scientific qualification.

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

## Final mandatory gates and installation

Independent review returned CODE_ACCEPT for the exact accepted code above and
PREREG_READY for the frozen new producer/reader plan. The following commands ran
once on that clean code, with no source/test/packaging/authority edits:

| Command | Result | Exit |
|---|---|---:|
| python -m pytest --basetemp .local/v/occfinal | 1487 passed, 840.85 seconds | 0 |
| python -m ruff format --check . | 236 files already formatted | 0 |
| python -m ruff check . | All checks passed | 0 |
| python -m mypy src tests | No issues in 184 source files | 0 |
| python scripts/scan_cae_data.py --root . | PASS, 238 checked | 0 |
| python -m build | sdist and wheel built | 0 |
| Fresh Python 3.12.10 -I -m venv | New isolated environment | 0 |
| Installed Python -I -m pip install --no-index --no-deps --no-cache-dir with the two pinned local wheels | Noneditable installation | 0 |
| Absolute installed febio-cae --version | febio-cae 0.1.0 | 0 |

The old product wheel was archived and verified before build: 284562 bytes,
SHA256 7b1747c84d5d18ce06ecef4070768e6b14616d689cb4e3cee8314355fa207caa.
The new product wheel is 284609 bytes, SHA256
e95aa28e74a254bf280aac5110b8c48c08186dbcdbae894b8c737a7d2eb28ba6.
All 95 Python members match current source and installed bytes exactly, and
accepted Git content after newline normalization. Actual imported product module
and namespace origins are inside the new environment. System site packages are
disabled, child PYTHONPATH/PYTHONHOME removed, and the official Gmsh module/DLL
hashes match the preceding table without native import during the audit.
Exact argv, UTC, Python, working directories and paired raw logs remain ignored
local evidence. Earlier attempts and the original environment are preserved.

Only this report changes after the accepted code; code gates apply through
unchanged source/test/packaging/authority Git objects and normalized tracked
content, with a separate final report boundary scan. These passing synthetic
and installation gates do not imply real geometry success.

## Separately released new producer: failed

After all mandatory gates and installation binding passed, the producer received
an explicit release and ran exactly once: child/wrapper exit 1, pending cleanup
0, 60-second ceiling, CPU 1 and owned memory ceiling 1 GiB. The unchanged helper
raised RuntimeError("unexpected serialized point or scale") at serialized point
admission. STEP bytes were written, but producer.json was not produced.
The failed STEP is 15377 bytes, SHA256
64c647f43b7027b1abc32b0235c151af1482cacad03a049953adcaede4c8ca14.
This identifies failed evidence, not an admitted geometry fixture.

Static inspection of saved bytes finds the first rejected entity is
#39 = CARTESIAN_POINT('',(0.,0.)). It is referenced by LINE #38 in
DEFINITIONAL_REPRESENTATION #37, with explicit 2D parametric context #42, used by
PCURVE #31. The preregistered regex inspected every CARTESIAN_POINT and required
three coordinates, thereby confusing auxiliary 2D curve points with 3D solid
vertices. Saved bytes declare AP214 and millimetres. No native query was added.

Smallest proposed next preregistration correction, not applied: distinguish
explicitly typed/context-bound auxiliary 2D points from solid vertex coordinates,
and apply the unchanged 3D bounds/eight-corner checks to CARTESIAN_POINT entities
referenced by the solid's VERTEX_POINT topology; reject malformed or ambiguous
references. Do not rescale, relabel units or loosen numerical tolerances.

The failure location shows finalize-return and the post-finalize initialized
check were traversed, but the measured producer success record was never saved.
No saved BuildInfo, volume, centroid, bounds or successful corner-admission result
is claimed for this attempt. The public reader was not released or run; no
registry/case was created. New session ledger: producer 1, reader 0, mesh 0;
historical failed producer 1 and identity diagnostic 1 remain separate.
No retry, helper/product/test edit or additional native operation was performed.

## Pending work

Independent final artifact/evidence review and PM integration/nonforce push remain
pending. Any corrected preregistration and new producer/reader execution require a
new review and release. Public SI topology, source/inspection digests, unchanged
draft/generation/profile and reader cleanup remain unexecuted. Native
qualification remains UNVERIFIED. Geometric/scientific qualification, profile,
FB03/FBS, AI02/API, figures, Studio/Computer Use, required actual E2Es and final
BottomFrame remain pending; this is not project completion.
