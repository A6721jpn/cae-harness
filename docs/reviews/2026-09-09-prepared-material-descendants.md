# Prepared material descendants: code stage

Code-stage historical status: CODE_REVIEW_PENDING; mandatory final acceptance gates PENDING at that stage. Current results are appended below.

Base: `9c7095c4899f6b83c4c68fa38ae73688160e3e5c` (accepted/pushed V2).
Authority update: `11e14c6d21e6ce61dd904eb0506605095b2ce265`.
Test-first commit: `14feceb09570f7a92bccd386fdfce1ae5d645d9c`.

The public case patch, validate/freeze and run-demo preflight path now reuses an
immutable producer-owned PREPARED root for registered explicit Young modulus
changes. Each ancestor's registered bytes, parent spec digest, mesh profile and
source evidence are verified. Restoring only E and its evidence must reproduce
the parent's entire spec; other top-level evidence must remain unchanged.
Original root adoption is checked before existing adoption binds the child.
Root receipt/mesh publication and current draft admission remain unchanged.

Changed product files: storage/preparation.py, application/service.py (prepared
inspection lookup), application/_demo.py (prepared inspection lookup).
Coverage: tests/component/application/test_planar_preparation.py; both authority
documents describe this bounded behavior. No native execution or remesh occurs.

## Focused evidence

- RED: `python -m pytest tests/component/application/test_planar_preparation.py -k prepared_material_child --basetemp .local/v/pc1r`: 1 failed, 8 deselected, exit 1; registered child preflight required the root-only receipt.
- GREEN: `python -m pytest tests/component/application/test_planar_preparation.py tests/component/application/test_planar_edit_validation.py --basetemp .local/v/pc1g`: 10 passed, exit 0.
- Final GREEN after extending the storage snapshot lease: same command with `--basetemp .local/v/pc1g2`; 10 passed, exit 0.
- `python -m ruff format --check .`: 220 files, exit 0.
- `python -m ruff check .`: exit 0 after preserving ValueError for a valid but unsupported material value (initial TRY004, exit 1).
- `python -m mypy src tests`: 172 source files, exit 0.
- `python scripts/scan_cae_data.py --root .`: initial 222 tracked files, exit 0; final staged report scan recorded in the local handoff.

Exact raw logs, argv, Python 3.12.10 executable, SHA and dirty state are in ignored
`.local/verification/pc1-*.json` and `.log`; final clean SHA and applicability are
in `.local/verification/prepared-material-descendants-code-handoff.md`.

## Remaining required work

Independent exact-commit Medium CODE review is next. Full `python -m pytest`,
`python -m build`, and a fresh virtual environment wheel installation followed
by `febio-cae --version` are PENDING for the subsequent acceptance stage after
review fixes settle. Native Gmsh/OCCT/FEBio/Studio, live LLM, required real E2Es
and final BottomFrame E2E remain unverified by this synthetic code stage.
REMOTE_CONFIGURED: https://github.com/A6721jpn/cae-harness.git; PM integration
branch V2; worker has not merged or pushed. This is not project completion.

## Final local acceptance gates (2026-09-09)

Current status: local gates PASS; final exact-evidence/document review and PM
acceptance/integration PENDING. Independent Medium CODE_ACCEPT covered exact
`e63776ca384600c4e556690199c87ee56c3c4241` with no findings. All commands below
ran on that clean fixed candidate; no executable changes or gate failures were
introduced in this stage. Python was 3.12.10.

| Command | Fresh result |
|---|---|
| `python -m pytest --basetemp .local/v/pcf1` | 1475 passed in 748.77s, exit 0 |
| `python -m ruff format --check .` | 221 files formatted, exit 0 |
| `python -m ruff check .` | PASS, exit 0 |
| `python -m mypy src tests` | 172 source files, exit 0 |
| `python scripts/scan_cae_data.py --root .` | 223 tracked/index files, no issues, exit 0 |
| `python -m build` | New sdist and wheel built, exit 0 |
| `python -m venv .local/verification/pcf-env` | Fresh Python 3.12 environment, exit 0 |
| Fresh environment `python -m pip install --no-index --no-deps dist/febio_cae-0.1.0-py3-none-any.whl` | Installed exact new wheel, exit 0 |
| Fresh environment `febio-cae --version` from fresh cwd | `febio-cae 0.1.0`, exit 0 |
| Fresh environment `python -I ../pcf_installed_check.py ../../v/pcf1/test_stale_prepared_generation0 ../../../dist/febio_cae-0.1.0-py3-none-any.whl` | One isolated installed scenario, 9 public CLI calls, exit 0 |

New wheel: `dist/febio_cae-0.1.0-py3-none-any.whl`, 262456 bytes,
SHA256 `ff0367c038604aab82b3e98d23e368e9ffc859cc255ee4339ab13345849e3799`.
The wheel is identified by the new build log and hash, not its reusable filename.

Installed verification used the full suite's synthetic stale-prepared case after
that individual test completed, while unrelated tests continued. Only that
isolated temporary case was advanced; product bytes stayed fixed. No test module
or `src` product was imported by the installed script: relevant imports resolved
under the fresh environment's site-packages, matched the wheel bytes, and every
loaded `febio_cae` module was checked for installed origin. Generation entrypoints
were forbidden-call guards; the compiler returned a synthetic empty bundle.
Admission, patching, validation, freezing, ancestry, storage and CLI status code
were not stubbed. This proves installed preflight admission, not native solving
or compiler output correctness.

The ordinary public CLI refused the stale root with CONFLICT/8, accepted the
explicit E child through patch/validate/freeze and PREFLIGHT_PASSED/0, then
refused the registered mesh-size descendant with INVALID_INPUT/2. Exactly one
compiler admission occurred; native/remesh requests were zero. Root preparation
JSON and revision bytes were unchanged, root publication still identified the
original revision, and child nodes/elements/faces/quality matched the root mesh.

Paired ignored `pcf-*` logs/JSON preserve exact expanded argv, cwd, Python,
before/after SHA and dirty state, times and exits. Installed origins, CLI payloads,
root receipt hashes and wheel hash are in `pcf-installed-boundary.log` and the
condensed `pcf-installed-summary.json`. Final report SHA and applicability are in
`.local/verification/prepared-material-descendants-final-handoff.md`.

This report/plan update changes no product or test bytes from CODE_ACCEPT.
PM's exact final evidence/document review, V2 integration checks and non-force
push are next. Native Gmsh/OCCT/FEBio/Studio, live LLM, required real/native E2Es
and final BottomFrame remain unverified. The project is not declared complete.
