# Generated AP214 schema admission correction

BASE: 113387191d7ba419d0932e997d2e65cc56452860.
RED test commit: 5c571905f6a231a4390040a76a72699f3eda004a.
The exact final CODE commit and raw command provenance are in the ignored code
handoff. This is a candidate for independent review, not native qualification.

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

## Pending work

Independent exact CODE review precedes one converged full suite, build and
fresh corrected-wheel installation. Those mandatory gates are PENDING and were
not run here. Ignored native04 preparation reuses the accepted immutable03
producer/STEP as input; it creates no environment and runs no native operation.
Only output/state/venv paths move to04, with explicit03 input references.
Corrected wheel and installed-byte identities remain pending future build.

The future single reader remains held until CODE acceptance, all final gates,
fresh artifact binding and separate PM release. No producer regeneration,
native probe, mesh, retry, assertion or tolerance change is included. The old03
reader stays failed; synthetic GREEN does not retroactively establish success.
Native qualification, profiles/scientific support, FB03/FBS, AI02, figures,
Studio/Computer Use, required actual E2Es and BottomFrame remain unverified.
