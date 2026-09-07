# P1 common interface freeze: repaired whole-candidate handoff

Date: 2026-09-08

This is a clean, report-bearing repair candidate for independent whole-candidate
review. It is a local synthetic contract handoff, not P1 completion, execution
authority, native capability, official FBS, FEBio Studio, real-model,
02_CAE, or BottomFrame evidence.

## Fixed Git boundary

| Item | Value |
|---|---|
| Worker branch | codex/p1-interface-freeze |
| Accepted integration base | 52745ef387f8c4466d9d7216bcf8e9532589b1b6 |
| Initial test-only contract SHA | 4da9e922d84519a009bd59c46aadd8d7e23a2e1f |
| Synthetic fixture correction SHA | ab1340b5e884fc975383d871d03c011434817f57 |
| Rejected candidate reviewed | 0fe6fc6b630775c6934d95c01e0b2fa68de0d665 |
| Repair regression-test SHA | ceafa3d26eccfb9d86195bf4fc7a2a0a8bdaf1b5 |
| Mesh/data/port repair SHA | 677c9d6fa1d4e71fd75c21bc83546bdf5bf75d1f |
| Strict codec repair SHA | 4f01dd5077d0c6ad8ee175ca963449ef185daf6d |
| Connected-chain test SHA | 689b078cea923ef095f50c534acd98f894b81391 |
| Static-cleanup SHA | c8a02026c1018bb4e66686b9ffc3b1340c8c72e6 |
| Final report-bearing candidate before this report commit | c8a02026c1018bb4e66686b9ffc3b1340c8c72e6 |
| Authorized remote | https://github.com/A6721jpn/cae-harness.git |
| Remote state | REMOTE_CONFIGURED |
| Integration/push | not performed by this worker |

The independent review of 0fe6fc6 found six High and three Medium findings.
This repair addresses those findings in one common-owner sequence. The PM must
still obtain a fresh independent whole-candidate review before integration.

## Repair scope

- H1/H2: MeshSet now uses a compatible typed identity for each set kind.
  The common mesh projection defines Tet10 corner/edge/oriented-face tables,
  source-body ownership, selection provenance, and structural face/set
  references. It validates nonempty face sets, local face IDs, oriented
  connectivity, body ownership, duplicate IDs, and cross-body references.
  This is structural contract validation only; it does not prove native
  meshing, Jacobians, or CAD correspondence.
- H3: strict nested decoding now rejects unknown keys and nonnull values for
  absent patch edits instead of silently dropping input. The same explicit
  schema ownership is retained through the existing child constructors.
- H4: the explicit codec registry and encoder now cover IssuedQuestion,
  OperationStatus, FileEntry, and PreviewRequest, in addition to the
  existing draft/spec/revision and workflow records.
- H5: logical file paths reject ambiguous Windows components and file
  collections reject case-insensitive identity collisions. Runtime
  containment, reparse-point, handle, and registration checks remain the
  responsibility of the later trusted storage/application layer.
- H6: the common boundary now includes verified SourceAssetContent,
  SourceAssetResolverPort, geometry selection resolution,
  ResolvedFileContent, ResultDataRef, NumericResultData, ResultDataPort,
  and injected result data for QualityPort. A synthetic consumer retrieves
  actual source bytes and numeric state/value data through these typed seams.
- M1: comparison axes carry a finite ordered common interval with matching
  units and an explicit interpolation method.
- M2: preview confirmation requires explicit observed states/variables or
  preserves already observed values; it never synthesizes observations from
  requested values.
- M3: the historical gate attribution is corrected below. The original
  candidate's static checks and smoke are not presented as clean evidence for
  the later commit.

## Frozen API and ownership split

- artifacts.py owns SourceAssetRef, SourceAssetContent,
  GeometryInspectionRequest, GeometryInspection, GeometrySelectionRequest,
  canonical SI mesh nodes/elements/oriented faces/sets, Tet10 ordering/mapping
  identities, mesh provenance/quality records, FileEntry, and
  ResolvedFileContent.
- execution.py owns immutable ExecutionBundle, AttemptRecord, process
  identity, primitive settings, file bindings, and owner-generation/run/attempt
  links. Value construction does not authorize process execution or descendant
  drain.
- results.py owns validated reader output, ResultManifest, typed
  measurements, numeric result records, criterion assessments, and
  QualityAssessment. Execution, numerical, and applicability dimensions
  remain separate.
- comparison.py owns identity/condition/axis declarations, including common
  interval and interpolation; it implements no comparison algorithm.
- preview.py owns PreviewRequest and PreviewReceipt. Launch, observed
  data, and independent confirmation evidence remain distinct.
- compatibility.py owns tool identities, capability status/evidence, output
  mappings, signs, frames, units, measures, and CompatibilityProfile.
  Unknown capability remains unsupported or unverified.
- lifecycle.py owns preparation/task/preview/run states, legal transitions,
  service diagnostics, structured exit categories/codes, and OperationStatus.
- ports.py owns the typed adapter/application seams. The repaired signatures
  include:

  ~~~text
  SourceAssetResolverPort.resolve(SourceAssetRef) -> SourceAssetContent
  GeometryPort.inspect(GeometryInspectionRequest, SourceAssetContent)
      -> GeometryInspection
  GeometryPort.resolve_selection(GeometrySelectionRequest, SourceAssetContent)
      -> ResolutionSnapshot
  ResultDataPort.resolve_file(FileEntry, ExecutionBundle, AttemptRecord)
      -> ResolvedFileContent
  ResultDataPort.resolve(ResultDataRef) -> NumericResultData
  ResultDataPort.resolve_manifest_output(manifest_id, output_id)
      -> NumericResultData
  QualityPort.assess(manifest, revision, mesh, profile, ResultDataPort)
      -> QualityAssessment
  ~~~

  Runner, reader, preview, case-registry, compatibility-registry, and
  ownership ports remain interfaces only. No caller-supplied approved or
  ready boolean is an authorization API.
- codec.py remains the single explicit schema-1 registry. It rejects
  unknown/duplicate/missing keys, wrong schema versions, bool-confused integer
  fields, nonfinite numbers, mappings in record positions, arbitrary dispatch,
  and digest mismatches before constructing validated domain values.
- domain/__init__.py is the deliberate public re-export surface; it does not
  create a second serializer or record owner.

Application/storage work still owns source/evidence registration, draft CAS,
one-time question/patch application, immutable persistence, filesystem and
reparse checks, process ownership, atomic publication, and receipt invalidation
after file mutation. No such runtime implementation is part of this candidate.

## Exact code/test delta from the accepted base

The current candidate changes exactly 17 tracked paths from
52745ef387f8c4466d9d7216bcf8e9532589b1b6:

- docs/reviews/2026-09-08-p1-interface-freeze.md
- src/febio_cae/domain/__init__.py
- src/febio_cae/domain/artifacts.py
- src/febio_cae/domain/case_patch.py
- src/febio_cae/domain/codec.py
- src/febio_cae/domain/comparison.py
- src/febio_cae/domain/compatibility.py
- src/febio_cae/domain/execution.py
- src/febio_cae/domain/lifecycle.py
- src/febio_cae/domain/ports.py
- src/febio_cae/domain/preview.py
- src/febio_cae/domain/questions.py
- src/febio_cae/domain/results.py
- tests/unit/contracts/test_adapter_ports.py
- tests/unit/contracts/test_contract_codec.py
- tests/unit/contracts/test_workflow_records.py
- tests/unit/contracts/workflow_fixtures.py

No storage, registration, application/controller, solver, reader
implementation, native tool, GUI, dependency, or real CAE data path was
changed.

## Test-first evidence

The initial contract RED was run at 4da9e922 with:

~~~text
python -m pytest tests/unit/contracts/test_workflow_records.py tests/unit/contracts/test_contract_codec.py tests/unit/contracts/test_adapter_ports.py --basetemp=.local/verification/P1-IF-red-01
~~~

It collected 19 tests, failed 3, skipped 16, and exited 1. The failures were
the deliberate API-availability assertions; this was a valid behavioral RED,
not a collection or environment failure. The synthetic digest-literal repair
at ab1340b5 then produced 13 focused passes.

The repair RED at ceafa3d was recorded as P1-IF-R1-red-01 with the same three
focused test files and a fresh pytest-temp-01 basetemp. It collected 30 tests,
with 9 failures, 10 passes, and 11 skips; exit 1. The failures reproduced the
review defects before production repair.

The repaired focused GREEN is record P1-IF-R1-green-final-01:

~~~text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_workflow_records.py tests/unit/contracts/test_contract_codec.py tests/unit/contracts/test_adapter_ports.py --basetemp .local/verification/P1-IF-R1-green-final-01
~~~

It collected 31 tests, passed 31, and exited 0.

## Fresh clean local gates

All records below captured clean before and after status at
c8a02026c1018bb4e66686b9ffc3b1340c8c72e6, before the report-only commit.

| Record | Exact command | Result |
|---|---|---|
| P1-IF-R1-full-final-01 | C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp .local/verification/P1-IF-R1-full-final-01 | 1,037 passed in 21.93 s, exit 0 |
| P1-IF-R1-format-final-01 | C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check . | 99 files already formatted, exit 0 |
| P1-IF-R1-lint-final-01 | C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check . | all checks passed, exit 0 |
| P1-IF-R1-mypy-final-01 | C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests | no issues in 67 source files, exit 0 |
| P1-IF-R1-scan-final-01 | C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root . | PASS; 101 tracked/index files, 0 diagnostics, exit 0 |
| P1-IF-R1-build-final-01 | C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build | sdist and wheel built, exit 0 |

## Fresh wheel and installed smoke

The exact wheel built from the repaired candidate is:

~~~text
dist/febio_cae-0.1.0-py3-none-any.whl
size: 100710 bytes
SHA256: EF719A3E945CBEA753AE4931361304B2DBD858554C1F0A316AED810A4C767DBD
~~~

The fresh environment and install records are:

~~~text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m venv .local/verification/P1-IF-R1-wheel-venv-final-01
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-IF-R1-wheel-venv-final-01\Scripts\python.exe -I -m pip install --no-index --no-deps --force-reinstall dist/febio_cae-0.1.0-py3-none-any.whl
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-IF-R1-wheel-venv-final-01\Scripts\febio-cae.exe --version
~~~

The venv and wheel install both exited 0. The executable was run from
.local/verification/P1-IF-R1-installed-cwd-final-01, outside the source
package path, and printed febio-cae 0.1.0 with exit 0. The isolated public
construction record was:

~~~text
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-IF-R1-wheel-venv-final-01\Scripts\python.exe -I -c "installed codec/ports construction and origin assertion"
~~~

Record P1-IF-R1-installed-import-final-01 exited 0 with PYTHONPATH and
PYTHONHOME unset. It verified the import origin under the fresh venv's
site-packages, round-tripped a minimal CaseDraft through the public codec,
and constructed the public owner/runner port vocabulary. This is synthetic
package evidence, not native capability or runtime authority.

## Unverified boundaries and next task

This handoff does not verify registered state/authority, source/evidence
registration, CAS, durable storage, filesystem/reparse safety, process
ownership, descendant drain, FEBio input compilation, solver execution, XPLT
reading, numeric quality calculations, atomic result publication, FEBio Studio
confirmation, an enabled native compatibility profile, official FBS, or any
real 02_CAE/BottomFrame E2E. Synthetic geometry and numeric resolver tests
prove only the frozen typed seam.

Next task: obtain an independent review of this exact final report-bearing
candidate, then PM-only integration into V2, fresh integrated gates, and
only afterward consumer work on the B/C paths. This worker does not integrate,
push, or contact the reviewer directly.
