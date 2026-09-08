# P2 input/model geometry and mesh adapter handoff

Date: 2026-09-08

Status: historical original handoff, subsequently rejected in independent review.
Its M3 execution chronology is corrected below from preserved command events;
the original commit and raw evidence remain unchanged. See the current
[consolidated source handoff](2026-09-08-p2-c-source-handoff.md) for fresh gates.
This slice is synthetic/local evidence only. It is not P2 closure, official FBS,
FEBio execution, FEBio Studio evidence, real-model evidence, `02_CAE` evidence,
or BottomFrame evidence. The worker did not integrate or push.

## Fixed Git boundary

| Item | Value |
|---|---|
| Accepted V2 base | `c23dc59d4dae4810acd51fe2156b7d190d645acb` |
| Test-only unit/fixture commit | `cce3f2ec1000f98f984dc0f8b0ee98e996a8a1c9` |
| Production implementation commit | `c7422677497c775c43b3754d421bc72b33088e76` |
| Final code/test candidate before this report | `1f2bc1fc52524b6363e334176ef120c6f1ac3b56` |
| Worker branch state | detached `HEAD` at the final code/test candidate before this report |
| Authorized remote | `https://github.com/A6721jpn/cae-harness.git` |
| Remote state | `REMOTE_CONFIGURED` |
| Integration/push | not performed by this worker |
| Report commit SHA | emitted in the final worker handoff after the additive report-only commit |

The final code/test candidate changes exactly these ten paths relative to the
accepted base; this report is the eleventh path in the report-bearing commit:

```text
src/febio_cae/adapters/geometry/__init__.py
src/febio_cae/adapters/geometry/adapter.py
src/febio_cae/adapters/geometry/backend.py
src/febio_cae/adapters/geometry/gmsh_occ.py
src/febio_cae/adapters/meshing/__init__.py
src/febio_cae/adapters/meshing/primitives.py
tests/component/geometry/__init__.py
tests/component/geometry/conftest.py
tests/component/geometry/test_adapter.py
tests/component/geometry/test_gmsh_units.py
```

No domain contract, `pyproject.toml`, common initializer, application/CLI,
solver/FBS, storage, real `02_CAE` directory, credential, native executable,
or legacy repository/worktree/asset was changed.

## Implemented product boundary

- The optional Gmsh/OpenCASCADE backend is dynamically loaded only when used,
  checks the configured module/version/kernel/frame, and maps structured
  environment, capability, input, integrity, and quality failures.
- STEP inspection retains the declared length unit as provenance, sets
  `Geometry.OCCTargetUnit="M"` before `importShapes`, and converts native
  coordinates and measurements through one explicit SI boundary. The source
  declaration is not applied a second time.
- Inspection reports observed bodies, boundary faces, closure, volume, area,
  centroids, attributes, and stable geometry/source digests. No physical
  meaning is inferred from names, order, orientation, or geometry convention.
- Meshing reads Gmsh type-11 Tet10 elements, rejects unsupported or malformed
  arrays and non-positive corner volumes, applies the explicit
  `tet10-backend-v1` permutation, and returns SI `MeshArtifact` data with
  boundary facet source-face provenance and one/two-element adjacency.
- The adapter consumes the frozen domain ports, resolves registered source and
  selection values, supports the bounded sphere/cylinder/box primitive path,
  applies the canonical Tet10 map, and preserves node/element ownership across
  disjoint generated bodies.

This implementation boundary is not a CAD/FEA acceptance claim. The only
native-related regression in the tracked tests uses a deterministic mocked
Gmsh module.

## Test-first evidence

Commands below used working directory
`C:\Users\backo\.codex\worktrees\2ef6\CAE-harness`. Original shell events
recorded bare `python` invocations, not independently expanded absolute child
executables. Later installed-smoke wrapper records explicitly captured their
absolute child argv; these two evidence formats must not be conflated.

### Availability RED chronology

At base `c23dc59d4dae4810acd51fe2156b7d190d645acb`, the three recorded RED
commands were:

```text
python -m pytest tests/component/geometry --basetemp C:\Users\backo\.codex\worktrees\2ef6\CAE-harness\.local\verification\P2-input-red-01
python -m pytest tests/component/geometry --basetemp C:\Users\backo\.codex\worktrees\2ef6\CAE-harness\.local\verification\P2-input-red-02
python -m pytest tests/component/geometry --basetemp C:\Users\backo\.codex\worktrees\2ef6\CAE-harness\.local\verification\P2-input-red-03
```

`P2-input-red-01` collected 17 tests and exited `1`: two intentional API
availability failures and 15 skips. `P2-input-red-02` collected 17 and exited
`1`: one API availability failure and 16 skips. `P2-input-red-03` collected
17 and exited `1`: one API availability failure and 16 skips. There were no
collection, setup, or environment failures. The failed assertions were the
missing `StepGeometryMeshAdapter` API; RED-01 also reached the missing
`GmshOCCConfig` API assertion. These are genuine nonzero RED results, not
passing evidence.

The focused unit RED for OCC target-unit ordering was recorded against the
in-progress implementation, before that behavior was corrected:

```text
python -m pytest tests/component/geometry/test_gmsh_units.py --basetemp .local/verification/P2-input-green-08-red -q
```

It exited `1` because the deterministic fake requires the explicit target unit
before import. That is behavior-failure evidence, not a clean-source attribution.
The focused GREEN occurred in a format/lint/types/test shell chain before the
production commit (preserved event ordinal 1604):

```text
python -m pytest tests/component/geometry --basetemp .local/verification/P2-input-green-10 -q
```

It passed 18 tests in 0.12 s, with final shell exit `0`. It was not a run at
clean `1f2bc1f`. Production was later committed separately in `c742267`; the
test package initializer was subsequently added while that commit was checked
out, then committed in `1f2bc1f` after the full test/type/format/lint runs.

## Historical local gates: corrected M3 chronology

The full test, mypy, and format/lint outputs are genuine but belong to `c742267`
plus the uncommitted new `tests/component/geometry/__init__.py`, not clean
`1f2bc1f`. Preserved events 1744, 1751, 1758 and 1765 precede the initializer
commit at 1772. Scanner and build occur afterward (1781 and 1788). This is a
chronology correction, not a reconstruction or rerun of the rejected candidate.

| Original shell command | Result | Captured exit and state |
|---|---|---|
| `python -m pytest --basetemp .local/verification/P2-input-full-03 -q` | 1,066 passed, 21.80 s | 0; dirty initializer at c742267 |
| `python -m ruff format --check .; python -m ruff check .` | 109 formatted; all lint checks passed | final shell 0; individual format exit not separately captured; dirty initializer |
| `python -m mypy src tests` | success in 77 files | 0; dirty initializer |
| `python scripts/scan_cae_data.py --root .` | PASS; 111 checked, 0 diagnostics | 0; after 1f2bc1f |
| `python -m build` | sdist and wheel built | 0; after 1f2bc1f |

Source: PM-preserved `P2-geometry-whole-review-01/evidence-audit.json`, selected
original command/output pairs and independent M3 finding. The new consolidated
execution manifest hashes that preserved audit and supplies fresh clean gates.

The full suite and all static gates are synthetic/local evidence. They do not
establish a real Gmsh/FEBio/FBS/Studio execution path.

## Built wheel and installed consumer smoke

The wheel built at the final code/test candidate is:

```text
Wheel: C:\Users\backo\.codex\worktrees\2ef6\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
Bytes: 128346
SHA-256: B4A8796131E8471A5291933096BDC125AD9E921F24687FC71EA9FC8F053DB9E8
```

The SHA-256 line above is the recorded wheel digest for the artifact used by
the smoke. A fresh venv was created outside the checkout at
`C:\Users\backo\AppData\Local\Temp\cae-harness-P2-input-installed-01`.
The outside-cwd commands used the actual checkout only as `git_metadata_cwd`
for SHA/dirty-state capture; every child command used absolute executables and
the wrapper explicitly unset `PYTHONPATH` and `PYTHONHOME`.

| Record | Expanded child command/result | Exit |
|---|---|---:|
| `P2-input-installed-preflight-01` | external root existed and was empty | 0 |
| `P2-input-installed-venv-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m venv C:\Users\backo\AppData\Local\Temp\cae-harness-P2-input-installed-01\venv` | 0 |
| `P2-input-installed-wheel-hash-03` | isolated absolute Python `hashlib` read of the wheel above; 128346 bytes and the recorded SHA-256 | 0 |
| `P2-input-installed-pip-01` | `C:\Users\backo\AppData\Local\Temp\cae-harness-P2-input-installed-01\venv\Scripts\python.exe -m pip install --no-index --disable-pip-version-check --no-cache-dir --no-deps --force-reinstall C:\Users\backo\.codex\worktrees\2ef6\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl`; `Successfully installed febio-cae-0.1.0` | 0 |
| `P2-input-installed-cli-01` | `C:\Users\backo\AppData\Local\Temp\cae-harness-P2-input-installed-01\venv\Scripts\python.exe -I -m febio_cae --version`; `febio-cae 0.1.0` | 0 |
| `P2-input-installed-import-01` | `C:\Users\backo\AppData\Local\Temp\cae-harness-P2-input-installed-01\venv\Scripts\python.exe -I -c <origin assertion>`; `isolated=1`, Python 3.12.10, version `0.1.0`, and `febio_cae`, `domain`, `adapters.geometry`, and `adapters.meshing` all imported from the external venv's `site-packages` | 0 |

The exact wheel-hash child argv for `P2-input-installed-wheel-hash-03` was:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -I -c "import hashlib, json, pathlib; p=pathlib.Path(r'C:\Users\backo\.codex\worktrees\2ef6\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl'); b=p.read_bytes(); print(json.dumps({'wheel':str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()},sort_keys=True))"
```

The exact import-origin child argv for `P2-input-installed-import-01` was:

```text
C:\Users\backo\AppData\Local\Temp\cae-harness-P2-input-installed-01\venv\Scripts\python.exe -I -c "import json, pathlib, sys; import febio_cae, febio_cae.domain as domain, febio_cae.adapters.geometry as geometry, febio_cae.adapters.meshing as meshing; modules=(febio_cae, domain, geometry, meshing); origins={module.__name__:str(pathlib.Path(module.__file__).resolve()) for module in modules}; venv=pathlib.Path(sys.prefix).resolve(); assert all(pathlib.Path(origin).is_relative_to(venv) for origin in origins.values()); assert not any(\"CAE-harness\" in origin for origin in origins.values()); print(json.dumps({\"python\":sys.version.split()[0],\"isolated\":sys.flags.isolated,\"prefix\":str(venv),\"version\":febio_cae.__version__,\"origins\":origins},sort_keys=True))"
```

The import assertion required every reported module origin to be below the
external venv prefix and rejected any checkout path. This is an installed
package/consumer smoke, not native or real-model evidence.

Two earlier hash probes are retained only as limitations: `P2-input-installed-
wheel-hash-01` exited `0` but its child shell lacked `Get-FileHash` and emitted
`sha256=null`; `P2-input-installed-wheel-hash-02` exited `1` due malformed
quoting. Neither is counted. The corrected `-03` Python hash record is the
only wheel-digest evidence used above.

## Native and real-model boundary

No Gmsh, FEBio, FEBio Studio, FBS, native executable, or real `02_CAE` input
was launched or accessed for this handoff. The ignored native qualification
proposal/runner remains `PENDING_AUTHORIZATION` and must not be interpreted as
completed qualification. In particular, the current native item still has
unverified or missing evidence for:

- authoritative STEP export-unit control and a captured native input fixture;
- complete Gmsh module/DLL/runtime identity and hash capture;
- `StepGeometryMeshAdapter` inspection and `WholeBodyRule` selection resolution;
- a measured bidirectional CAD/mesh boundary deviation, rather than a point or
  area sanity check;
- real CAD topology/closure/volume/face correspondence and mesh quality;
- FEBio input compilation, solver execution, official FBS authority, XPLT
  collection, FEBio Studio confirmation, and real-model/BottomFrame E2E.

The deterministic test fixture and the adapter's primitive path remain synthetic
evidence. They do not prove physical material/load/constraint/contact meaning,
native compatibility, official authority, or project completion.

## Handoff and next task

After the additive report-only commit, PM should pin the exact report-bearing
SHA and obtain an independent whole-candidate review. Only a whole `ACCEPT`
should be integrated into `V2`; PM then reruns fresh post-integration gates and
pushes without force. This worker will not integrate, push, launch native tools,
or access real CAE data.
