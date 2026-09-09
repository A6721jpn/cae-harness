# P4 intent source: CODE_READY evidence

Status: CODE_READY for independent Medium CODE review; P4/project completion is
not claimed. REMOTE_CONFIGURED is `https://github.com/A6721jpn/cae-harness.git`.
The worker made no merge or push. The accepted base is
`57caa690695959f805d31e17112bea623a20fa11`.

## Scope and commits

Both designated specification authorities now freeze the bounded P4 contracts.
The public commands are `case intent`, `case answer`, and isotropic-E-only
`case edit`, with explicit generation, operation ID and settings. Whole affirmative
clauses and registered same-case component adoption are independently grounded;
unsupported or contradictory material facts remain unresolved. Complete components
use the existing draft/question/patch authority and existing validate/freeze.

The Responses adapter uses a closed schema, identical common count/generation
fields, explicit model and memory-only key resolution, byte/CPU/memory/deadline
limits, and unchanged owned-process cleanup. The case-local operation journal
claims IDs before HTTP, retains conservative reservations and measured usage,
rejects changed re-entry, and never resends or reapplies an existing claim.
Application leases cover context checks and publication, never HTTP. Existing
service changes are limited to source-preceding CAS checks, thin entrypoints and
classification of missing numerical configuration.

| Commit | Purpose |
|---|---|
| `2581e619fd9331a9540830714986b34505f64635` | Docs-first contract freeze and plan-13 status correction |
| `77fadd42ee5a73bdaa674f9933ea2eb04f7b7f00` | Test-first behavioral RED and callable adapter scaffold |
| `a9f02c4` | Literal syntax/settings documentation |
| `024b835` | Explicit settings contract and bounded Responses adapter |
| `187d3b3` | Independent grounding and operation journal |
| `c5fcfb04f2d5c60cf95bd732479aa05c17b97132` | Public application/CLI flow and final source/test tree |

Owned source: `application/intent_contracts.py`, `_intent.py`,
`_intent_grounding.py`, narrow `application/service.py` changes,
`adapters/llm/{__init__,openai_responses}.py`, `storage/llm_operations.py`,
`cli/{main,case}.py`. Tests are the two `tests/component/autonomy/test_*.py`
files and their small `intent_fixture.py`. Frozen domain DTOs/codecs, existing
storage primitives and the geometry process helper are unchanged. No dependencies
were added. An initial conftest shim was replaced by the named fixture to avoid
mypy duplicate-module collection.

## Executed source evidence

Python: 3.12.10, Windows x64. Cwd was the same isolated V2 candidate checkout.
Ignored `.local/verification/p4-*-meta.json` files record exact cwd, UTC,
command, SHA/dirty state and exit codes; corresponding `.log` files retain raw
output. Development commands ran against dirty working trees before their small
commits; the final 19-test tree was committed unchanged as `c5fcfb0`. Static gates
below ran on clean `c5fcfb0`. Later report/index edits do not change source/tests.

| Evidence | Exact command | Result |
|---|---|---|
| RED | `python -m pytest tests/component/autonomy --basetemp .local/v/p4r1` | 7 collected: 6 missing-behavior failures, 1 existing process-helper pass; exit 1 |
| Focused GREEN | `python -m pytest tests/component/autonomy --basetemp .local/v/p4g1` | 7 passed; exit 0 |
| Final affected GREEN | `python -m pytest tests/component/autonomy tests/component/application/test_registered_service.py tests/component/application/test_registered_r2.py::test_h8_answer_changes_only_issued_targets tests/component/cli/test_case_patch_cli.py --basetemp .local/v/p4affected` | 19 passed, including the same 7 autonomy tests; exit 0 |
| Format | `python -m ruff format --check .` | 230 files formatted; exit 0 |
| Lint | `python -m ruff check .` | passed; exit 0 |
| Types | `python -m mypy src tests` | 181 source files; exit 0 |
| CAE boundary | `python scripts/scan_cae_data.py --root .` | 232 tracked files at source commit; PASS, exit 0 |

The directly affected existing tests cover generation CAS, concurrent service
writers, immutable source registration, one-shot questions, answer target limits,
and public typed patch/diagnostic behavior. Counts are not summed across reruns.
The final combined run includes public CLI validate/freeze and the real adapter
owned-launch path with an injected Python-only hanging child. It includes no live
HTTP or native CAE tool.

Earlier failures are retained: the first test-file syntax/collection error
(exit 2) is not RED evidence; the first implementation run exposed unsupported
standalone Budget decoding, fixed by reusing the frozen PartialCaseSpec codec;
a later run exposed freeze-normalized selection metadata, fixed by binding to
the registered frozen generation plus exact current draft digest. Static iterations
also caught the fixture-module collision, import/style and type issues. None of
these failed runs are counted as successful evidence.

## Limits and next task

This is source and isolated synthetic evidence only. The production HTTPS child,
actual OpenAI response/model behavior, credentials, native Gmsh/OCCT/FEBio/Studio,
real `02_CAE`, and BottomFrame were not exercised. The hanging child verifies
local ownership/deadline behavior, not remote cancellation or billing. The count
allowance is a local reservation; missing/uncertain usage retains capacity.

Full `python -m pytest`, `python -m build`, and fresh-wheel installation followed
by installed `febio-cae --version` are REQUIRED and PENDING until Medium CODE
review settles. No gate is waived. Next: PM commissions read-only Medium review
of the exact final candidate, then this same coder handles any fixes; after CODE
acceptance, run all mandatory gates and fresh installed public-boundary evidence,
then exact evidence review and PM-only V2 integration/push. AI-02 and every
required real/native E2E, including final BottomFrame, remain separate unverified
gates requiring their authorized execution.


## Fix 1 after independent CODE_REJECT (M1 / M2)

The independent review of `612c65799e5b23ca5a9cf08a7b0cdb10578612c4`
identified two P2 defects. Fix source commit `b0beef56fdac34deec357a327e5d746ef53d6d24` follows test-first commit
`cd2063c9eff86aea91ce0d30d3a3d5db5ffbbe1c` and is ready for sequential CODE rereview, not accepted or integrated.

M1: the E-only CasePatch now attaches only its `material.youngs_modulus` evidence,
which still references the complete immutable edit source. Generic intent evidence
remains available to initial intent and answer; PREPARED ancestry and fixed-evidence
restrictions are unchanged. The existing synthetic prepared-child fixture now also
exercises the natural-language route through public edit, validate/freeze and
prepared preflight, retaining the existing mesh-change rejection and unchanged
root receipt/mesh checks. This is isolated synthetic preparation/compiler evidence.

M2: sanitized OSError, including TimeoutError, maps to environment diagnosis and
public exit 4. Reservation/usage recording and no-resend behavior are unchanged;
invalid numerical settings and malformed proposals remain input failures. The
existing public timeout/re-entry assertion now expects exit 4 and environment.

Paired focused commands (Python 3.12.10, same candidate cwd):

- RED: `python -m pytest tests/component/autonomy tests/component/application/test_planar_preparation.py::test_prepared_material_child[natural] --basetemp .local/v/p4fix1r`
  collected 8, with exactly M1 and M2 failing (2 failed / 6 passed), exit 1.
- GREEN: `python -m pytest tests/component/autonomy tests/component/application/test_planar_preparation.py::test_prepared_material_child[natural] --basetemp .local/v/p4fix1g`
  passed all 8, exit 0. Counts are not added to earlier runs.

Raw logs and exact SHA/dirty/UTC metadata are `p4fix1-red*` and `p4fix1-green*`
in ignored `.local/verification`. Static/CAE gate results, exact final candidate,
source/test hashes and preserved-boundary evidence are in the ignored
`p4-intent-source-fix1-handoff.md` there. The two authorities, domain/codec,
transport/process primitives and PREPARED storage authority remain frozen.

Full suite/build/fresh-wheel installation and final acceptance review remain
REQUIRED/PENDING until Medium accepts the fixed code. Live API, native/real E2Es
and final BottomFrame remain unverified; no credentials, real HTTP, native probe,
actual case data, external repository operation, merge or push occurred.
