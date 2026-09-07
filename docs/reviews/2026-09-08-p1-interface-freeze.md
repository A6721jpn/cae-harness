# P1 common interface freeze: R2 repaired whole-candidate handoff

Date: 2026-09-08

This is a clean, report-bearing R2 repair candidate for independent whole-candidate
review. It is synthetic contract evidence, not P1 completion, execution authority,
native capability, official FBS, FEBio Studio, real-model, `02_CAE`, or BottomFrame
evidence. The preceding candidate `fff4daf0a028526f19a1eec66be411d35b5e389f`
was rejected on H2, H5, and H6. This candidate closes those three finite findings
with new RED/GREEN evidence and is not yet integrated or pushed.

## Fixed Git boundary

| Item | Value |
|---|---|
| Worker branch | `codex/p1-interface-freeze` |
| Accepted integration base | `52745ef387f8c4466d9d7216bcf8e9532589b1b6` |
| Rejected R1 candidate | `fff4daf0a028526f19a1eec66be411d35b5e389f` |
| R2 test-only RED commit | `81b5888763b5bafa794d212f6477bdb6951cfe74` |
| R2 test expectation correction | `e3073b9a9a5cc00b3d0ce2c9fb9a43a883db4d13` |
| R2 mesh/path production commit | `bfab496b3df50f36efbd0a1e302d74c83180fe5f` |
| R2 numeric codec production commit | `0a8e015bf59c7aa17a1696a3929d6016ea25efa9` |
| R2 static/test cleanup commit | `60eea3385967d5579c8b271ff2c54930aca1572e` |
| Final R2 code/test candidate before this report commit | `05a047267898061b05326f87d432b06640be19ef` |
| Review ticket | `.local/coordination/p1-interface-review-02.md`, SHA256 `06ECDA4CACA56A957EFCA56B62FD74CF7FCB524C250A83095178CD05973C4F3B` |
| Authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| Remote state | `REMOTE_CONFIGURED` |
| Integration/push | not performed by this worker |

The original H1/H3/H4/M1/M2/M3 findings remain closed within their reviewed
scope. The R2 changes are limited to H2, H5, H6, their behavior-first tests, and
this report. PM must still obtain a fresh independent whole-candidate review.

## R2 repair scope

### H2 — oriented Tet10 boundary and ownership

`artifacts.py` now defines one uniform Tri6 convention: each face is ordered as
three cyclic corners followed by the consecutive midside edges `(a,b)`, `(b,c)`,
`(c,a)`. With the positive reference tetrahedron, the canonical face table is:

~~~text
(0, 2, 1, 6, 5, 4)
(0, 1, 3, 4, 8, 7)
(1, 2, 3, 5, 9, 8)
(0, 3, 2, 7, 9, 6)
~~~

The R2 tests use independently specified nondegenerate coordinates to verify all
four outward normal senses and every midside edge midpoint. A two-adjacent-element
interior face must use the exact reverse orientation
`(a,c,b,ca,bc,ab)` for the second element; a face may have at most two adjacent
elements. MeshSet body membership now requires `body_id` to be included in a body
set, and an isolated known node in a node set raises the domain validation error
instead of leaking `KeyError`. The tests also construct a valid two-body mesh with
nonempty node, element, face, and body sets, and reject contradictory ownership.
These are structural checks only; they do not prove native Gmsh/FEBio mapping,
Jacobian quality, or CAD correspondence.

### H5 — one strong logical-path rule

`validate_logical_path` is the shared public validator in `artifacts.py`, exported
through `domain.__init__`. `FileEntry` and `ResultDataRef` both use it, and nested
manifest decoding reaches the same `ResultDataRef` constructor. It applies one
relative POSIX rule plus Windows lexical checks for separators, drives/streams,
empty or dot components, trailing spaces/periods, invalid characters, and device
names. The four review repros are rejected through both direct references and
manifest decoding: `output/case.xplt:stream`, `output/NUL.xplt`,
`output./case.xplt`, and `output /case.xplt`. Filesystem containment, reparse,
handle, and trusted-registration checks remain later application/storage work.

### H6 — persistent numeric result boundary

`ResultDataRef` and `NumericResultData` now have public `to_bytes()` methods and
are registered in the single strict codec registry. `numeric-result-v1` encodes
the full non-self-referential binding: data ID, codec ID, logical path,
`bundle_digest`, and `attempt_id`, together with mapping, axis ID/unit/values,
entity and component ordering, and flattened values. Rows are state-major; each
row is entity-major, then component-major. The top-level content digest hashes
that projection without the digest field itself. Decoding rejects wrong schema or
codec, unknown keys, malformed width/shape, nonfinite values, and content-digest
mismatches; the constructor requires at least two increasing states and validates
the entity/component width.

R2 tests round-trip a payload with two states, two entities, and three components,
preserve mapping location/frame/measure/sign/order semantics plus bundle/attempt
binding, and reconstruct numeric records from encoded bytes in a resolver. The
resolver uses separate data-ID and manifest/output lookup keys, and a synthetic
QualityPort-shaped consumer reads both decoded records. The installed isolated
consumer repeats that boundary from the wheel and confirms site-packages origins.

The report wording is also corrected: protocol construction and actual encoded
numeric consumption are evidence; `hasattr`-only inspection is not presented as
RunnerPort construction. Runner, reader, storage, and native runtime authority
remain unverified.

## Frozen API and ownership split

- `artifacts.py` owns source/file content records, shared logical paths, geometry
  facts/selections, canonical Tet10 nodes/elements/faces/sets, mesh provenance,
  and structural quality records.
- `results.py` owns manifests, reader output, numeric data references/payloads,
  typed measurements, criteria, and quality assessments. Numeric data is now
  persistent through the common codec, but persistence authority is not implied.
- `ports.py` owns typed source, geometry, result-data, quality, runner, reader,
  preview, registry, and ownership seams. No caller-supplied `approved` or `ready`
  boolean grants authorization.
- `codec.py` remains the only explicit schema-1 record registry. It rejects
  unknown/duplicate/missing keys, wrong schemas, bool-confused integers,
  nonfinite numbers, arbitrary dispatch, and digest mismatches before constructors.
- `domain/__init__.py` remains the deliberate public re-export surface; it does
  not create a second serializer or record owner.

Application/storage work still owns source/evidence registration, draft CAS,
one-time question/patch application, immutable persistence, filesystem and
reparse checks, process ownership, descendant drain, atomic publication, and
receipt invalidation after file mutation.

## Exact code/test delta from the accepted base

The final candidate changes exactly these 17 tracked paths from
`52745ef387f8c4466d9d7216bcf8e9532589b1b6`:

- `docs/reviews/2026-09-08-p1-interface-freeze.md`
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

No storage, registration, controller, solver, reader implementation, native tool,
GUI, dependency, or real CAE data path was changed.

## Test-first evidence

The R2 test-only commit was `81b5888`. The fresh RED record is
`P1-IF-R2-red-01`:

~~~text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_workflow_records.py tests/unit/contracts/test_contract_codec.py tests/unit/contracts/test_adapter_ports.py --basetemp .local/verification/P1-IF-R2-red-01
~~~

It collected 37 tests, with 14 failures and 23 passes, and exited 1. The
failures are the intended pre-production regressions for the new table/path/codec
contract, not collection or environment failures. The final focused GREEN record
is `P1-IF-R2-green-final-01` at the clean final candidate:

~~~text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_workflow_records.py tests/unit/contracts/test_contract_codec.py tests/unit/contracts/test_adapter_ports.py --basetemp .local/verification/P1-IF-R2-green-final-01
~~~

It collected and passed 37 tests, exit 0.

## Fresh clean local gates

All final records below have clean before/after Git status at
`05a047267898061b05326f87d432b06640be19ef`.

| Record | Exact command | Result |
|---|---|---|
| `P1-IF-R2-full-final-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp .local/verification/P1-IF-R2-full-final-01` | 1,043 passed in 20.92 s, exit 0 |
| `P1-IF-R2-format-final-02` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .` | 99 files already formatted, exit 0 |
| `P1-IF-R2-lint-final-02` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .` | all checks passed, exit 0 |
| `P1-IF-R2-mypy-final-02` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests` | no issues in 67 source files, exit 0 |
| `P1-IF-R2-scan-final-02` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` | PASS; 101 tracked/index files, 0 diagnostics, exit 0 |
| `P1-IF-R2-build-final-02` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build` | sdist and wheel built, exit 0 |

## Fresh wheel and installed smoke

The exact wheel built from the final code candidate is:

~~~text
dist/febio_cae-0.1.0-py3-none-any.whl
size: 101520 bytes
SHA256: 5E9AB39E5EFAF2F7EF69DCAE04128D3432527D7BA8865852828699972F666E09
~~~

Fresh environment/install records:

~~~text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m venv .local/verification/P1-IF-R2-wheel-venv-final-02
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-IF-R2-wheel-venv-final-02\Scripts\python.exe -I -m pip install --no-index --no-deps --force-reinstall C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-IF-R2-wheel-venv-final-02\Scripts\febio-cae.exe --version
~~~

The venv and offline wheel install exited 0. The executable ran from
`.local/verification/P1-IF-R2-installed-cwd-final-02`, outside the source package
directory, printed `febio-cae 0.1.0`, and exited 0 (`P1-IF-R2-cli-final-02`).

The isolated numeric consumer is `P1-IF-R2-installed-consumer-final-04`; its exact
argv, cwd, unset `PYTHONPATH`/`PYTHONHOME`, Git SHA, and raw stream paths are in
`.local/coordination/runs/P1-IF-R2-installed-consumer-final-04/metadata.json`.
It exited 0 with these raw outputs:

~~~text
installed_origins=ok
numeric_codec_bytes=744
resolved_value=0.2
related_output_id=manifest-output-data
~~~

The script used `-I`, verified both imported module origins are in the fresh
venv's `site-packages` and not the source `src` tree, encoded/decoded both
`ResultDataRef` and `NumericResultData`, reconstructed the primary and related
manifest/output records from bytes, verified the content digest/bundle/attempt
binding, and passed the decoded port to a synthetic `QualityPort` consumer. This
is synthetic package evidence, not native capability, durable storage, or physical
numeric correctness.

## Unverified boundaries and next task

This handoff does not verify registered authority/CAS, source/evidence
registration, durable storage, filesystem/reparse safety, process ownership,
descendant drain, FEBio input compilation, solver execution, XPLT reading, numeric
quality algorithms, atomic result publication, FEBio Studio confirmation, an
enabled native compatibility profile, official FBS, or any real `02_CAE`/
BottomFrame E2E. The Tet10 checks are mathematical/structural contract tests;
they do not establish native Gmsh/FEBio ordering or Jacobian quality. Numeric
codec and resolver checks do not establish a durable backend or a physical result.

Next task: PM obtains an independent whole-candidate review of the exact
report-bearing candidate, then PM-only integration into V2 and fresh integrated
gates. This worker does not integrate, push, or contact the reviewer directly.
