# P1 common interface freeze: R3 bounded repair handoff

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

Date: 2026-09-08

This is a clean, report-bearing R3 repair candidate for independent whole-candidate
review. It is synthetic contract evidence, not P1 completion, execution authority,
native capability, official FBS, FEBio Studio, real-model, `02_CAE`, or BottomFrame
evidence. The preceding R2 candidate `a3ea6031972ac4123b5654ad363bd016b849c29b`
was rejected only on cyclic interior-face orientation and the single-state numeric
regression. This candidate repairs those finite findings, preserves the reviewed
H5/H6 binding/path work, and is not yet integrated or pushed.

## Fixed Git boundary

| Item | Value |
|---|---|
| Worker branch | `codex/p1-interface-freeze` |
| Accepted integration base | `52745ef387f8c4466d9d7216bcf8e9532589b1b6` |
| Rejected R1 candidate | `fff4daf0a028526f19a1eec66be411d35b5e389f` |
| Rejected R2 report-bearing candidate | `a3ea6031972ac4123b5654ad363bd016b849c29b` |
| R3 test-only commit | `16693381ee251d2d719a2d38e158b67493ff44f9` |
| R3 test-fixture correction commits | `9e63cf1a961d47cb09209d4d819974d9d753f86e`, `dd08ea93e3cd856a7f8e71240729cb1ae712e89a` |
| R3 cyclic-face production commit | `2449748da4ee8799a8cd81391c567c91f12d1b6d` |
| R3 single-state production commit | `015df77349a8b03170ed09eb1bff2d36498b5c9f` |
| R3 format cleanup commit | `ede6abbc3542dbc79290a651a0bebba55557de43` |
| Final R3 code/test candidate before this report commit | `ede6abbc3542dbc79290a651a0bebba55557de43` |
| Review ticket | `.local/coordination/p1-interface-review-03.md`, SHA256 `FCF1B69B8549FCEF2F2156C61F68E6A619329A7B9F2372D7B49548A552964579` |
| Authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| Remote state | `REMOTE_CONFIGURED` |
| Integration/push | not performed by this worker |

The original H1/H3/H4/M1/M2/M3 findings remain closed within their reviewed
scope. H5 and the original H6 serialization/binding findings remain closed from
R2. The R3 changes are limited to the two review-03 findings, their behavior-first
tests, and this report. PM must still obtain a fresh independent whole-candidate
review.

## Preserved R2 scope and R3 repair

### H2 — oriented Tet10 boundary, cyclic interiors, and ownership

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
four outward normal senses and every midside edge midpoint. R3 keeps that table
and extends the interior validator from one reversed tuple to all three cyclic
rotations of `(a,c,b,ca,bc,ab)`: `(c,b,a,bc,ab,ca)` and
`(b,a,c,ab,ca,bc)`. New tests construct positive-determinant neighboring
tetrahedra with shared local-face indices `1`, `0`, and `3`, using independent
edge midpoint nodes. Same-facing, wrong-midside, unknown-node, and over-two-
adjacency cases remain rejected. MeshSet body membership remains the documented
owner-inclusion rule, and an isolated known node in a node set raises the domain
validation error instead of leaking `KeyError`. These are structural checks only;
they do not prove native Gmsh/FEBio mapping, Jacobian quality, or CAD
correspondence.

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
mismatches; the constructor requires a nonempty state/row sequence, strictly
increasing axis values when multiple states exist, and the entity/component width.

R2 tests round-trip a payload with two states, two entities, and three components,
preserve mapping location/frame/measure/sign/order semantics plus bundle/attempt
binding, and reconstruct numeric records from encoded bytes in a resolver. The
resolver uses separate data-ID and manifest/output lookup keys, and a synthetic
QualityPort-shaped consumer reads both decoded records. R3 adds a public codec
round-trip for one state tied to a complete `CaseSpec`/`OutputPolicy` with one
saved time and one evaluation time. The installed isolated consumer reconstructs
one-state and two-state payloads from encoded bytes, verifies their digests and
binding, constructs a valid cyclic interior face, and confirms site-packages
origins. Interpolation or other algorithm-specific sufficiency rules remain
consumer-owned.

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

The R3 behavior-first test commits are `1669338`, `9e63cf1`, and `dd08ea9`.
The first two recorded RED attempts are preserved but are not acceptance RED
evidence because their newly added fixture helper still raised `KeyError` before
the intended assertion:

| Record | SHA | Result |
|---|---|---|
| `P1-IF-R3-red-01` | `16693381ee251d2d719a2d38e158b67493ff44f9` | 42 collected; 5 failed / 37 passed; exit 1; fixture helper errors preserved |
| `P1-IF-R3-red-02` | `9e63cf1a961d47cb09209d4d819974d9d753f86e` | 42 collected; 4 failed / 38 passed; exit 1; baseline fixture correction still incomplete |

After the fixture-only correction, `P1-IF-R3-red-03` at
`dd08ea93e3cd856a7f8e71240729cb1ae712e89a` is the genuine behavioral RED:

~~~text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_workflow_records.py tests/unit/contracts/test_contract_codec.py tests/unit/contracts/test_adapter_ports.py --basetemp .local/verification/P1-IF-R3-red-03
~~~

It collected 42 tests, with 3 intended failures and 39 passes, and exited 1.
The failures are the two cyclic face cases and the valid single-state numeric
case; there were no collection, setup, or environment failures. The final focused
GREEN record is `P1-IF-R3-green-final-01` at clean SHA
`ede6abbc3542dbc79290a651a0bebba55557de43`:

~~~text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/contracts/test_workflow_records.py tests/unit/contracts/test_contract_codec.py tests/unit/contracts/test_adapter_ports.py --basetemp .local/verification/P1-IF-R3-green-final-01
~~~

It collected and passed 42 tests, exit 0.

## Fresh clean local gates

All final records below have clean before/after Git status at the final code/test
SHA `ede6abbc3542dbc79290a651a0bebba55557de43`.

| Record | Exact command | Result |
|---|---|---|
| `P1-IF-R3-full-final-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp .local/verification/P1-IF-R3-full-final-01` | 1,048 passed in 22.39 s, exit 0 |
| `P1-IF-R3-format-final-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .` | 99 files already formatted, exit 0 |
| `P1-IF-R3-lint-final-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .` | all checks passed, exit 0 |
| `P1-IF-R3-mypy-final-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests` | no issues in 67 source files, exit 0 |
| `P1-IF-R3-scan-final-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` | PASS; 101 tracked/index files, 0 diagnostics, exit 0 |
| `P1-IF-R3-build-final-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build` | sdist and wheel built, exit 0 |

## Fresh wheel and installed smoke

The exact wheel built from the final code candidate is:

~~~text
dist/febio_cae-0.1.0-py3-none-any.whl
size: 101600 bytes
SHA256: 684F7078E3D8FE44150C87852401CF8B2191ADBEC8FE37959F54859D712C8624
~~~

Fresh environment/install records:

~~~text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m venv .local/verification/P1-IF-R3-wheel-venv-final-01
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-IF-R3-wheel-venv-final-01\Scripts\python.exe -I -m pip install --no-index --no-deps --force-reinstall C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P1-IF-R3-wheel-venv-final-01\Scripts\febio-cae.exe --version
~~~

The venv and offline wheel install exited 0. The executable ran from
`.local/verification/P1-IF-R3-installed-cwd-final-01`, outside the source package
directory, printed `febio-cae 0.1.0`, and exited 0 (`P1-IF-R3-cli-final-01`).

The isolated installed consumer is `P1-IF-R3-installed-consumer-final-01`; its
exact argv, cwd, unset `PYTHONPATH`/`PYTHONHOME`, Git SHA, and raw stream paths are
in `.local/coordination/runs/P1-IF-R3-installed-consumer-final-01/metadata.json`.
It exited 0 with these raw outputs:

~~~text
installed_origins=ok
cyclic_face=ok
single_roundtrip=ok states=1
multi_roundtrip=ok states=2 value=0.2
single_codec_bytes=717
~~~

The script used `-I`, verified both imported module origins are in the fresh
venv's `site-packages` and not the source `src` tree, constructed a positive
cyclic interior Tet10 face with a different local-face index, encoded/decoded
single-state and multi-state `NumericResultData` through the public codec, and
resolved both from separate encoded payload maps through the public
`ResultDataPort` seam. It verified content digests plus bundle/attempt binding.
This is synthetic package evidence, not native capability, durable storage, or
physical numeric correctness.

## Unverified boundaries and next task

This handoff does not verify registered authority/CAS, source/evidence
registration, durable storage, filesystem/reparse safety, process ownership,
descendant drain, FEBio input compilation, solver execution, XPLT reading, numeric
quality algorithms, atomic result publication, FEBio Studio confirmation, an
enabled native compatibility profile, official FBS, or any real `02_CAE`/
BottomFrame E2E. The Tet10 checks are mathematical/structural contract tests;
they do not establish native Gmsh/FEBio ordering or Jacobian quality. Numeric
codec and resolver checks do not establish a durable backend or a physical result.

## Report-only integrity checks

After the report commit, `P1-IF-R3-report-scan-final-01` reruns the CAE boundary
scanner and `P1-IF-R3-report-diff-final-01` checks clean status, the exact 17-path
delta from the accepted base, `git diff --check`, and the report byte/hash record.
Both are report-integrity evidence only and are not product test counts.

Next task: PM obtains an independent whole-candidate review of the exact
report-bearing candidate, then PM-only integration into V2 and fresh integrated
gates. This worker does not integrate, push, or contact the reviewer directly.
