# P1 common interface freeze review

Date: 2026-09-08

Scope: the remaining schema-1 common records, strict codec, lifecycle vocabulary,
structured errors, and minimal typed service ports. This is a local synthetic contract
handoff. It is not P1 completion, execution authority, native capability, official FBS,
FEBio Studio, real-model, `02_CAE`, or BottomFrame evidence.

## Fixed Git boundary

| Item | Value |
|---|---|
| Worker branch | `codex/p1-interface-freeze` |
| Accepted integration base | `52745ef387f8c4466d9d7216bcf8e9532589b1b6` |
| Initial test-only contract SHA | `4da9e922d84519a009bd59c46aadd8d7e23a2e1f` |
| Synthetic fixture correction SHA | `ab1340b5e884fc975383d871d03c011434817f57` |
| Production candidate SHA | `5999998ca66657da9330c06fbde8953972c2e7c0` |
| Test-only type-gate correction SHA | `f15e9c27c7d10039a74a1f857577dd15d70c0564` |
| Final pre-report SHA | `f15e9c27c7d10039a74a1f857577dd15d70c0564` |
| Authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| Remote state | `REMOTE_CONFIGURED` |
| Integration/push | not performed by this worker |

The code/test delta from the accepted base is exactly these 16 tracked paths before this
report; the report is the only additional path in the final handoff:

- `src/febio_cae/domain/__init__.py`
- `src/febio_cae/domain/artifacts.py`
- `src/febio_cae/domain/case_patch.py`
- `src/febio_cae/domain/codec.py`
- `src/febio_cae/domain/comparison.py`
- `src/febio_cae/domain/compatibility.py`
- `src/febio_cae/domain/execution.py`
- `src/febio_cae/domain/lifecycle.py`
- `src/febio_cae/domain/ports.py`
- `src/febio_cae/domain/preview.py`
- `src/febio_cae/domain/questions.py`
- `src/febio_cae/domain/results.py`
- `tests/unit/contracts/test_adapter_ports.py`
- `tests/unit/contracts/test_contract_codec.py`
- `tests/unit/contracts/test_workflow_records.py`
- `tests/unit/contracts/workflow_fixtures.py`

No storage, registration, application/controller, CLI, solver, reader implementation,
native tool, GUI, dependency, or real CAE data path was changed.

## Frozen API and ownership split

- `questions.py` defines immutable `IssuedQuestion` records bound to case, draft,
  generation, target fields, and complete question-time evidence.
- `case_patch.py` defines parent-revision/content-bound `CasePatch` and `CasePatchEdit`
  values for the eleven typed top-level fields. `present=False` is distinct from an
  explicit child value; there is no JSONPath, XML, code, or physical inference.
- `artifacts.py` owns `SourceAssetRef`, geometry-only inspection request/result records,
  canonical SI mesh nodes/elements/oriented faces/sets, Tet10 ordering/mapping identity,
  mesh provenance/quality records, and the shared relative logical `FileEntry`.
  Structural constructors validate finite values, duplicate IDs, and local references;
  they do not prove CAD correspondence, native mapping, or Jacobian success.
- `execution.py` owns immutable `ExecutionBundle`, `AttemptRecord`, process identity,
  and primitive settings. Bundle/attempt records bind case, revision, spec/mesh/profile,
  files, process provenance, run/attempt IDs, and owner generation without authorizing a
  process or claiming descendant drain.
- `results.py` owns validated reader output, `ResultManifest`, typed measurements,
  criterion assessments, and `QualityAssessment`. Execution, numerical, and applicability
  dimensions remain separate and quality is not publication authority.
- `comparison.py` owns identity/condition/axis declarations only; it implements no
  comparison algorithm.
- `preview.py` owns `PreviewRequest` and `PreviewReceipt`. Launch, observed state and
  variable data, and independent confirmation evidence are separate; a receipt does not
  become trusted confirmation authority by construction.
- `compatibility.py` owns `ToolIdentity`, capability status/evidence, output mappings,
  signs, frames, units, measures, and `CompatibilityProfile`. Unknown capability stays
  unsupported/unverified; there is no enabled-native-profile side effect.
- `lifecycle.py` owns preparation/task/preview/run states, guarded transitions, service
  diagnostics, structured exit categories/codes, and `OperationStatus`. It preserves
  interruption, cancellation, recovery, readiness, quality, and unsupported-environment
  distinctions.
- `ports.py` owns typed `GeometryPort`, `MeshingPort`, `CompilerPort`, `RunnerPort`,
  `ResultReaderPort`, `QualityPort`, `PreviewPort`, case/profile/ownership registries,
  `TrustedOwnerContext`, typed poll/cancel/reconcile outcomes, and `PortError`. Ports are
  interfaces only; no caller-supplied `approved` or `ready` boolean is an authorization
  API.
- `codec.py` is the single explicit schema-1 JSON registry for existing
  `CaseDraft`/`PartialCaseSpec`/`CaseSpec`/`CaseRevision` and the common records above.
  It rejects duplicate/unknown/missing keys, wrong schema versions, type-confused bools,
  non-finite numbers, mappings as records, arbitrary dispatch, and digest mismatches, then
  constructs validated domain values through their existing constructors and canonical
  projections.
- `domain/__init__.py` deliberately re-exports the new records, errors, enums, ports, and
  codec functions without creating a second owner.

Application/storage work still owns evidence registration, CAS, one-time question/patch
application, generation checks, immutable persistence, filesystem/reparse checks, process
ownership, atomic publication, and receipt invalidation after file mutation.

## Test-first evidence

The initial focused command was run from a clean test-only contract commit:

```text
python -m pytest tests/unit/contracts/test_workflow_records.py tests/unit/contracts/test_contract_codec.py tests/unit/contracts/test_adapter_ports.py --basetemp=.local/verification/P1-IF-red-01
```

At `4da9e922`, pytest collected 19 items and exited 1 with `3 failed, 16 skipped`.
The three failures were the deliberate API-availability assertions for workflow records,
codec, and ports; the skipped tests were dependent contract checks. This was a behavioral
RED with no collection or environment error.

The synthetic digest-only fixture repair was committed as `ab1340b`; its focused
workflow/ports rerun exited 0 with 13 passed. It changed only invalid synthetic literals
to valid lowercase hexadecimal digests and did not relax a production rule.

After the production implementation and export/type-gate corrections, the required fresh
GREEN command was:

```text
python -m pytest tests/unit/contracts/test_workflow_records.py tests/unit/contracts/test_contract_codec.py tests/unit/contracts/test_adapter_ports.py --basetemp=.local/verification/P1-IF-green-01
```

Result: collected 19, `19 passed in 0.07s`, exit 0. The connected synthetic chain covers
mesh → bundle → owned attempt → manifest → quality/preview identity, file/path and digest
validation, legal transitions, preview evidence, strict codec round-trips/rejections, and
port boundaries.

## Clean local gates

All commands below ran from clean committed code at `f15e9c2`; each exited 0.

| Gate | Exact command | Result |
|---|---|---|
| Full tests | `python -m pytest --basetemp=.local/verification/P1-IF-full-final` | 1,025 passed, 19.90 s |
| Format | `python -m ruff format --check .` | 98 files already formatted |
| Lint | `python -m ruff check .` | all checks passed |
| Types | `python -m mypy src tests` | no issues, 67 source files |
| CAE boundary | `python scripts/scan_cae_data.py --root .` | PASS; 100 tracked/index files, 0 diagnostics |
| Build | `python -m build` | sdist and wheel built successfully |

One earlier full-test invocation used a new basetemp whose ignored parent directory had
not yet been created; pytest reported setup `FileNotFoundError` before dependent tests ran.
That environmental attempt is not counted. The fresh final invocation above used a created
parent and is the only full-suite evidence reported.

## Wheel and installed smoke

The exact built artifact was:

```text
dist/febio_cae-0.1.0-py3-none-any.whl
size: 96306 bytes
SHA256: 634B071D574464936B46640DFB7F39D7654E275B61CA82516511CD735E5D7F4B
```

A fresh `.local/verification/P1-IF-wheel-venv-01` virtual environment installed that
wheel with `pip install --no-deps --force-reinstall` (exit 0). Installed smoke then exited
0 with:

```text
febio-cae 0.1.0
isolated codec/ports PASS
```

The smoke imported the installed public `CaseDraft`, `PartialCaseSpec`, codec functions,
`TrustedOwnerContext`, `PortErrorCategory`, and `RunnerPort`; it round-tripped the minimal
draft and checked the owner context/port vocabulary. No source-tree import was used as the
smoke assertion.

## Unverified and next ownership

This handoff does not verify any real geometry, registered evidence, material/load/support
meaning, filesystem authority, CAS, persistent storage, process ownership, descendant drain,
FEBio input compilation, solver execution, XPLT reading, quality publication, FEBio Studio
confirmation, official FBS, native capability profile, or real BottomFrame model E2E. All
tests and the installed smoke are synthetic/local package evidence.

Next task: independent review of this exact report-bearing candidate, followed by PM-only
integration into `V2`, fresh integrated gates, and only then consumer work on the B/C paths.
