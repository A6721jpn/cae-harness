# Initial public native inspection — code stage

Base: `3f837e7337a1a7d089d2c2d71bdd8ea5ee595aa6`.
Contract-first: `b4dd5f38a51f58f05cf26ffe9048ea656cd4fb5e`.
Collected test-first: `bd64c44`.
Same-session measurement clarification: `8418866b32872fc545da4aa701a208d7358d2152`.
Product: `cc4d4c544dd7002d2f4a42f27aa08c8a057d8c35`.
Status: CODE_READY for independent exact-commit review, not CODE_ACCEPT.

`case --state-dir STATE inspect CASE_ID --native [--wall-seconds SECONDS]
[--cpu-workers N] --json` now inspects a newly registered source without a
GeometryIntent, selected body, physical conditions or profile. Metadata-only
inspect is unchanged. The response contains observed generation, the existing
domain geometry record, complete validated topology, measured backend identity,
effective limits and the usual diagnostic envelope. IDs remain source/inspection
scoped; INSPECTED is observation and native_qualification stays UNVERIFIED.

The operation uses one enclosing deadline (maximum/default 600 seconds), one
existing owned child, zero mesh generations and no retries. Request preparation,
resource acquisition, launch and response work consume remaining time. CPU caps
cannot exceed availability; memory retains the existing 80%-available ceiling.
Before response decoding, stat and limit bytes to min(16 MiB, memory/16), then
bound the read and validate JSON shape, finite values, count bounds, canonical
topology, source identity and independently reconstructed inspection digest.
Malformed/tampered response is integrity 6; version/AP214/timeout is environment
or unsupported 4, caller policy 2, and generation conflict 8.

Short existing snapshots surround source admission, the immediate prelaunch
check and final return check. No source/database lease spans child execution;
busy snapshot admission returns conflict rather than waiting on another long
operation. Inspection does not publish a draft, mesh, profile, producer receipt
or frozen/PREPARED state. Unique case-local scratch retains private operation
evidence; preparation still performs its own current inspection.

A private measurement-only GmshOCCBackend subclass calls unchanged session
admission then captures module/hash/version/BuildInfo in that same live owned
session. There is no planar face override, second native session or geometry
algorithm change. Gmsh 4.15.2, OCCT 8.0.1 and AP214 stay fixed. This composition
was explicitly clarified in both formal authorities before product work.

## Focused evidence

Python 3.12.10; cwd is the candidate checkout. All four tests use the existing
SyntheticBackend and a private owned-child seam, never actual native software.

| Command | Collected/result | Exit |
| --- | --- | --- |
| `python -m pytest tests/component/application/test_native_inspection.py --basetemp .local/v/insr` | 4 collected; 4 missing-public-behavior failures | 1 |
| `python -m pytest tests/component/application/test_native_inspection.py --basetemp .local/v/insg` | 4 passed, 1.56 seconds | 0 |
| `python -m pytest tests/component/cli/test_case_cli.py::test_case_cli_create_inspect_and_spec_use_registered_state --basetemp .local/v/insmeta` | 1 directly affected metadata regression passed | 0 |

RED UTC: 2026-09-09T09:01:42.4712987Z–09:01:43.5978407Z.
Final focused GREEN UTC: 2026-09-09T09:10:10.6814280Z–09:10:12.5150493Z.
The final source/test state from that GREEN was committed as the product SHA
above without another product edit. Metadata regression predates only the final
canonical geometry-byte comparison; its metadata path is unchanged.

The four boundaries cover initial topology/units/digest with unchanged draft and
no mesh; tampered report, concurrent generation and corrupted registered source;
unsupported version, invalid finite policy and oversized response; and the real
existing owned deadline/cleanup primitive with a synthetic hanging-process port.
The deadline case includes resource-work time in the same clock. No actual
child process or Gmsh module is started by this focused suite.

One first GREEN attempt had 1 failed/3 passed because CurrentInspection's source
mismatch became environment 4 in the existing adapter; explicit source-digest
admission before reconstruction corrected it to integrity 6. Initial lint/type
checks also identified exception-type conventions, a read-only protocol attribute
typing mismatch and test-local typing issues. Failed logs are retained and not
counted as passes. Later focused reruns followed only these changes and tightened
deadline/snapshot/canonical response checks; counts are not summed across runs.

Cheap checks: `python -m ruff format --check .`, `python -m ruff check .`,
`python -m mypy src tests`, `python scripts/scan_cae_data.py --root .`.
Exact final SHA, file/blob applicability, command times/counts/exits and raw
paths are recorded in the ignored code handoff. Source-stage format/lint/type
checks pass (234 formatted files; 184 typed source files); CAE boundary passed
with no excluded tracked files. Final report/index are included in the final scan.

## Limits and next task

Only both authorities, the inspect CLI/service entry, two new inspection modules,
the four-case test module and this report/index change. Domain/codec, geometry
algorithms, preparation producer/receipt, ownership primitives, stores, provider
and packaging remain frozen. REMOTE_CONFIGURED is the authorized cae-harness
remote; no worker merge/push or root AGENTS modification occurred.

Next: independent Medium exact CODE review, fixes if required, then all mandatory
full gates/build/fresh installed public smoke once after code acceptance, exact
artifact/evidence review and PM V2 integration/nonforce push. No full suite,
build or installation was performed in this source stage.

Actual Gmsh/OCCT/AP214 native inspection, trusted profile qualification authority,
FB-03/FBS, AI-02/live API, Studio/Computer Use, investigation figures, all actual
E2Es and final BottomFrame remain pending. There is no provisioning command or
placeholder catalog and no claim of complete REQ-04/P2/P6 or project completion.

## Fix 1: snapshot deadline classification after CODE_REJECT

Independent Medium rejected `2c4b445f94d5b824670fce8815f649695b481a4b`
for P2/I1: TimeoutError from an inspection deadline inside an evidence snapshot
crossed the existing storage OSError conversion and became integrity/exit 6.
The application now wraps only its own remaining-time check and converts
TimeoutError to an environment PortError before it crosses any storage context.
Storage errors are not classified by message text; actual corruption remains 6,
generation conflict remains 8, and the original shared deadline calculation,
adapter, process ownership and storage primitives are unchanged.

Test-first commit `8c9b580` extends the existing deadline test, keeping four
collected cases. A controlled clock advances while the real evidence snapshot
is active; the production remaining-time calculation expires before native
launch. The test does not replace the deadline function with an unconditional
exception. Existing owned-child timeout/cleanup and source-corruption/stale
generation assertions are retained.

| Exact command | Collected/result | Exit |
| --- | --- | --- |
| `python -m pytest tests/component/application/test_native_inspection.py --basetemp .local/v/insfix1r` | 4 collected; snapshot expiry failed (1 failed, 3 passed) | 1 |
| `python -m pytest tests/component/application/test_native_inspection.py --basetemp .local/v/insfix1g` | 4 passed, 1.59 seconds | 0 |

Raw paired logs and UTC/cwd/SHA/dirty/Python metadata are retained in ignored
`inspection-fix1-red*` and `inspection-fix1-green*` evidence. The final exact
commit, cheap format/lint/type/CAE results, changed-file hashes and unchanged
boundary proof are in the ignored fix-1 handoff. Only this report, the existing
test module and application/_inspection.py change in this correction.

Next is independent fixed-code rereview. Full mandatory gates, build, fresh
installed smoke, final evidence acceptance and PM integration/push remain
required and pending. All actual native/qualification/AI-02/figures/E2E and
BottomFrame gates remain pending; this is synthetic source evidence only.

## Final local gates after CODE_ACCEPT

Independent Medium accepted exact code `87d1b4385a5db002f79fc6cdea64d935a551a3c1` (I1 resolved).
All mandatory local gates below completed on that clean accepted candidate
under Python 3.12.10; source/tests/packaging and both authorities stayed frozen.

| Command | UTC start / end | Result | Exit |
| --- | --- | --- | --- |
| `python -m pytest --basetemp .local/v/insfinal` | 2026-09-09T09:29:03.4537744Z / 2026-09-09T09:43:07.5246291Z | 1487 passed | 0 |
| `python -m ruff format --check .` | 2026-09-09T09:29:03.4114986Z / 2026-09-09T09:29:03.6343082Z | 235 files formatted | 0 |
| `python -m ruff check .` | 2026-09-09T09:29:03.4710374Z / 2026-09-09T09:29:03.6940246Z | all checks passed | 0 |
| `python -m mypy src tests` | 2026-09-09T09:29:03.5097011Z / 2026-09-09T09:29:03.9419024Z | 184 source files | 0 |
| `python scripts/scan_cae_data.py --root .` | 2026-09-09T09:29:03.4446215Z / 2026-09-09T09:29:15.5290033Z | 237 tracked; PASS | 0 |
| `python -m build` | 2026-09-09T09:29:03.4557798Z / 2026-09-09T09:29:12.4786943Z | wheel and sdist | 0 |
| `python -m venv .local/ini` | 2026-09-09T09:31:24.4070284Z / 2026-09-09T09:31:29.4241022Z | previously absent Python 3.12.10 environment | 0 |
| `.local/ini/Scripts/python.exe -I -m pip install --no-index --no-deps dist/febio_cae-0.1.0-py3-none-any.whl` | 2026-09-09T09:31:29.5965337Z / 2026-09-09T09:31:31.3765343Z | noneditable new wheel install | 0 |
| `Fresh absolute febio-cae.exe --version` | 2026-09-09T09:32:29.7782493Z / 2026-09-09T09:32:30.0456644Z | febio-cae 0.1.0 | 0 |
| `Fresh Python -I; ignored installed public inspection helper` | 2026-09-09T09:32:31.9912077Z / 2026-09-09T09:32:32.9541769Z | 1 synthetic scenario, 3 public calls, 70 imports | 0 |

New wheel: 284562 bytes; SHA256
`7b1747c84d5d18ce06ecef4070768e6b14616d689cb4e3cee8314355fa207caa`.
A previously absent short-path venv received the new wheel without editable
installation or source shadowing. Absolute console version ran from scratch
with PYTHONPATH/PYTHONHOME unset for that child. Installed smoke used -I.

One finite synthetic public create -> native inspect -> metadata inspect
scenario passed three public commands. It verifies source-bound body/face/
unit/digest observations, unchanged draft/generation, zero compatibility and
mesh-quality registrations, no frozen revision and zero mesh generations.
Only existing SyntheticBackend and private child/resource seams are injected;
real installed CLI/service/source admission/serialization/reconstruction run.
All 70 loaded febio_cae module/namespace origins are fresh site-packages.
All 95 wheel Python members match accepted checkout and installed bytes.

The exact default installed child argv was also executed through unchanged
owned-process protection (5 seconds, 256 MiB). Its invalid private protocol
was rejected before produce/native loading with the exact integrity ERROR
response, child exit 0, empty stdout/stderr and no pending cleanup. Exit 0
here is successful transport of an expected rejection, not native success.
This verifies entrypoint/packaging/cleanup, not Gmsh/OCCT/AP214 capability.

There were no failed final-stage gate or installed-helper attempts.
Two report-generator attempts failed on PowerShell encodings (CP932 install
metadata and UTF-16 test log). Original records are retained; only local
decoding/config-string handling was corrected. No gate was rerun. Earlier
source-stage RED/fix failures remain in their original evidence and are not
recounted. No second full suite is required for the report-only final commit.
The ignored final handoff records exact FINAL, raw command metadata, wheel
member/source equivalence (Git content CRLF-normalized only), unchanged
tracked code/authority blobs and the changed-document CAE scan.

Independent Medium final artifact/evidence acceptance and PM-only V2
integration/nonforce push remain pending. Actual Gmsh/OCCT/AP214, trusted
qualification/FB03/FBS, AI02/live API, investigation figures, Studio/
Computer Use, every actual E2E and final BottomFrame remain unverified.
Local/installed synthetic success does not declare project completion.
