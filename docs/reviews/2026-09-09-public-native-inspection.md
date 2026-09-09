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
