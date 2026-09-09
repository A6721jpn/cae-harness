# Generated AP214 schema admission correction

BASE: 113387191d7ba419d0932e997d2e65cc56452860.
RED test commit: 5c571905f6a231a4390040a76a72699f3eda004a.
Accepted CODE: 65207c18f77c6d75453d637bf2a9723510b67f21.
Final mandatory gates and one fixed public inspection passed. Independent final
artifact review remains pending; native qualification remains UNVERIFIED.

The accepted native03 producer wrote the qualified schema identifier
AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }. The public reader refused it before
native module loading because the existing full-match pattern admitted only
the shorter edition suffix. The actual reader remains FAILED, CLI exit 4;
producer success and reader failure are separate accepted evidence.

The shared header checker now adds exactly the observed eight-arc alternative
alongside the existing bare name and seven-arc forms. Case-insensitive matching,
existing whitespace treatment, tokenized HEADER parsing, exactly one
FILE_SCHEMA, comment/string separation and full matching remain unchanged.
Other protocols and extra suffixes remain rejected. This does not make the
extra arc optional for every edition or accept arbitrary identifiers containing
214. The existing formal AP214/candidate-pair requirement is unchanged.

Only gmsh_occ.py, the existing test_gmsh_preparation.py and this report change.
Native versions, configurable OCCT tests, units, geometry, ownership, domains
and packaging are unchanged. No real STEP/model data or local task metadata is
committed.

## Minimal TDD and cheap gates

| Command | Result | Exit |
|---|---|---:|
| python -m pytest tests/component/geometry/test_gmsh_preparation.py::test_configured_preparation_precedes_import_and_cleans_up -q | RED: 2 collected failures, inspect and mesh at schema admission | 1 |
| python -m pytest tests/component/geometry/test_gmsh_preparation.py -q | GREEN: 8 passed | 0 |
| python -m ruff format --check . | Final: 236 files already formatted | 0 |
| python -m ruff check . | All checks passed | 0 |
| python -m mypy src tests | No issues in 184 files | 0 |
| python scripts/scan_cae_data.py --root . | 239 files checked, no issues | 0 |

The existing success loop adds the exact emitted literal using a tiny synthetic
HEADER. The existing refusal loop adds a similarly shaped wrong-standard OID
and an extra suffix. No new test file or matrix. RED was recorded and committed
before the product change; these are synthetic tests, not native execution.

Initial format check exited 1 for mixed line endings in the two edited Python
files. The read-only targeted format diff also exited 1. Targeted formatting
exited 0, with normalized before/after content hashes equal; only the format
check was repeated. GREEN, lint and types remain applicable to identical
normalized content. Raw commands, UTC, Python, counts and exits are retained
in ignored evidence; no failed attempt is counted as a pass.

## Final mandatory gates

Independent CODE_ACCEPT and PREREG_DESIGN_READY preceded these once-only gates
on clean accepted CODE. Source, tests, packaging and authorities stayed fixed.

| Command | Actual result | Exit |
|---|---|---:|
| python -m pytest --basetemp .local/v/ap214final | 1487 collected/passed; 0 failed/skipped; 875.56 seconds | 0 |
| python -m ruff format --check . | 237 files already formatted | 0 |
| python -m ruff check . | All checks passed | 0 |
| python -m mypy src tests | No issues in 184 files | 0 |
| python scripts/scan_cae_data.py --root . | 239 files checked, no issues | 0 |
| python -m build | sdist and corrected wheel built | 0 |
| Fresh isolated Python 3.12.10 venv and local wheel installation | Noneditable, no-index/no-deps/no-cache; system sites disabled | 0 |
| Absolute installed febio-cae --version | febio-cae 0.1.0 | 0 |

The old accepted wheel was archived and verified before build: 284609 bytes,
SHA256 e95aa28e74a254bf280aac5110b8c48c08186dbcdbae894b8c737a7d2eb28ba6.
The corrected wheel is 284623 bytes, SHA256
f7cd764a527429b81d8bf1bc174c2da362a5da6470eb7a147e04dcd2749afd63.
All 95 Python members match source/wheel/installed bytes and normalized CODE
content; all 62 actual product module/namespace origins are in the fresh
environment. Pinned Gmsh module/DLL bytes were verified without native import.
Child shadow paths were removed; setup steps were bounded to 120 seconds.

Reporting correction: the frozen installation-binding narrative said 236 files
for final format. Its actual raw log says 237, as shown above. The frozen
history remains retained; this corrects the transcription, not the result.
Exact argv, UTC, cwd, Python, exits and raw logs remain ignored local evidence.

## Separately released native04 fixed inspection

After all gates and the corrected installation binding passed, PM separately
released one reader using immutable successful03 producer/STEP bytes. No new
producer was generated. Actual create, before, native and after CLI operations,
native child, scenario, owner and outer command all exited 0; cleanup pending 0.
The public response was INSPECTED with Gmsh 4.15.2 / OCC 7.8.1 measured in this
reader session and the pinned installed module hash.

The single closed solid and all six face area/centroid pairs matched the fixed
SI analytic expectations and tolerances; source/geometry/inspection digests
were retained. Complete returned before/after metadata and drafts match at
generation 0. No resolved profile is separately exposed by these responses;
no independent profile-store snapshot or promotion is claimed. Qualification
remains UNVERIFIED. Public body bounds, body centroid and Plane types remain
unexposed producer-only facts.

New04 ledger: producer 0, reader attempts/initialized sessions 1, mesh 0.
The unchanged helper's producer1/reader1 field describes reused03 producer
plus new04 reader, not a new04 producer. Historical01 failed producer and
diagnostic,02 failed producer,03 successful producer and failed reader remain
distinct and unchanged. Cumulative initialized sessions: 5; reader attempts: 2.
No retry, extra native query, geometry change or tolerance adjustment occurred.

Only this report changes after CODE. Final applicability uses unchanged
source/test/packaging/authority Git objects and normalized tracked equality,
with a separate final report boundary scan; full suite/build are not rerun.

## Pending work

Independent final artifact review, PM fast-forward integration and nonforce
V2 push remain pending. This fixed synthetic-geometry inspection does not
qualify broad native/scientific/profile support, FB03/FBS, AI02, figures,
Studio/Computer Use, required actual E2Es or BottomFrame. Project completion
is not asserted.
