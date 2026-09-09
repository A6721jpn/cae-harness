# P0-B Gmsh native geometry observation

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

Date: 2026-09-07

Target: bounded synthetic OpenCASCADE geometry, STEP round-trip, and second-order tetrahedral mesh observation through the official Gmsh Python API.

Base: `11082b84b32982cbd2f8eed7d6a82eb2ee1f809a`

Branch: `codex/p0-b-gmsh-observation`

Remote: `https://github.com/A6721jpn/cae-harness.git` (`REMOTE_CONFIGURED`)

Tracked change: this Markdown file only. The task-local venv, scripts, preflight, correction record, native outputs, hashes, and provenance extract are ignored under `.local/verification/P0B-gmsh-observation-01`.

## 1. Bounded verdict / 判定

`P0-B GMSH NATIVE GEOMETRY/STEP/TET10 OBSERVATION RECORDED; PREFLIGHT ARITHMETIC CORRECTION RETAINED; FEBIO/MAPPING/PRODUCT/REAL-MODEL SUPPORT UNVERIFIED`

The official Gmsh 4.15.2 Python package was installed only into a dedicated Python 3.12 venv. Native API initialization succeeded in three scoped successful processes. The observation created three independent OCC volumes, exported and reimported a STEP file in a fresh process, and generated a second-order tetrahedral mesh with positive sampled Jacobian/quality measures.

This is not a FEBio input-mapping test, a solver test, a contact test, a product gate, a Studio/UI observation, or real `02_CAE` evidence.

## 2. Authority and official sources / 仕様境界

The repository authority remains the [V2 design specification](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md) and [greenfield plan](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md). This investigation does not amend either document.

The native API and element facts were checked against the [official Gmsh 4.15.2 manual](https://gmsh.info/doc/texinfo/gmsh.html), including OCC primitives, `gmsh.write`, `getElementProperties`, `setOrder`, `getElements`, and `getElementQualities`. The package and wheel metadata were checked against [official PyPI gmsh metadata](https://pypi.org/project/gmsh/) and the [4.15.2 JSON metadata](https://pypi.org/pypi/gmsh/4.15.2/json).

Before task-local installation, `gmsh` was absent from standard PATH and the base Python 3.12 import spec was absent. No global install, PATH edit, product dependency, or system change was made.

## 3. Dependency and environment evidence / 依存性

The dedicated environment is:

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-gmsh-observation-01\gmsh-venv\Scripts\python.exe
Python 3.12.10
gmsh 4.15.2
```

The exact lock is `gmsh-dependency-lock.txt`, with wheel SHA-256 `7B36083BB410FA27C5D0E052929D1A9844A5B09169D66017B72B41AABD49D711`. The official wheel URL is recorded in `native-environment.json`. The installed module SHA-256 is `A56EBE69DC57A3EA15EEE191CAE4F1881B06784174B2D9DF306A98BDD8B06606`; the loaded native library identity record is `gmsh-4.15.dll`, 89,212,416 bytes, SHA-256 `6CAC3EEFB477265D9FA60BBD869DBBBF7C7CA4CB308C0F8F2B43E91D43DE3C1C`.

The first attempted hash-locked install exited `1` because pip 25.0.1 rejected `--no-deps` inside the requirements file. That failure is retained in the session record. The lock was corrected without changing the package/version/hash, and the following task-local install exited `0`:

```text
<gmsh-venv>\Scripts\python.exe -m pip install --no-deps --require-hashes -r <absolute>\gmsh-dependency-lock.txt
```

The first environment-capture helper also failed to locate the wheel's DLL because it assumed the DLL was under `site-packages`; the helper was corrected to use the package's `Lib\gmsh-4.15.dll` location before native initialization. The final environment record explicitly states that its capture did not initialize the native API; initialization evidence comes only from the attempt records.

## 4. Frozen preflight and correction / 事前固定値

The preflight was written and made read-only before the first native attempt:

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-gmsh-observation-01\preflight.json
SHA-256: 9011584442ED7F2757E0C3EA2E179E5DD228045E83066765627C815C9E50E8E1
```

It fixes a right-handed synthetic coordinate frame, length unit `U`, a translated sphere, an oblique-axis cylinder, and a translated/rotated box. It fixes volume and bounding-box tolerances, six-attempt/120-second process bounds, two configured threads, and a 15-minute aggregate native budget. The OCC bodies remain separate; no Boolean fuse is called.

The first native geometry attempt exposed an arithmetic transcription error in the frozen cylinder `expected_volume` field. The preflight formula is `pi*r^2*norm(axis_vector)` with `r=0.65` and axis norm `2.6`, which evaluates to `3.451039529968388`; the hand-entered number was `3.4515488918268565`. The original preflight remains immutable. The correction is separately recorded at:

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-gmsh-observation-01\preflight-correction-01.json
SHA-256: 7FDD9E218258D361410BCDE2FEF025EF8BCFDDE2C38845A12FDF0724992FF567
```

The correction changes no geometry, tolerance, process budget, or acceptance rule. Attempt-01 and its diagnostic follow-up are not acceptance evidence; subsequent checks use formula-derived geometry values and retain both the original preflight and correction record.

The original source JSONL provenance extract is:

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-gmsh-observation-01\provenance-extract.json
SHA-256: E040748CED9DCB385EE58FD7C7A6A5CFE022CE858E127175EA9E3A7310C0FFDF
```

It retains the selected original JSONL lines and parsed objects for the preflight FileChange, validation, and all five native wrapper calls/events/outputs. It keeps response-item recording timestamps, CommandExecution parent fields, and child creation times in separate fields. The source whole-file SHA is a snapshot at extraction because the active session JSONL can append later.

The validation event's command records the read-only assignment/check, and the preflight file is read-only in the final filesystem evidence. The original command output did not preserve a rendered Boolean read-only line, so no stronger claim is made about that output field than the recorded command plus current immutable-file state.

## 5. Native attempt ledger / 実行台帳

The runner used a unique attempt directory, the task-local interpreter, the frozen 120-second timeout, configured thread limits of `2`, owned-child PID tracking, raw stdout/stderr files, and per-artifact SHA-256 records. No descendant-drain claim was made.

| Attempt | Purpose | Exit | Duration | Evidence status |
|---|---|---:|---:|---|
| `attempt-01-create-export` | initial OCC create/export with frozen numeric field | `1` | `0.250 s` | retained failure; not accepted |
| `attempt-02-diagnostic-geometry` | inspect actual OCC mass after the arithmetic mismatch | `0` | `0.250 s` | diagnostic only; not accepted |
| `attempt-03-create-export-corrected` | corrected OCC create/export | `0` | `0.281 s` | scoped geometry evidence |
| `attempt-02-step-reimport` | fresh-process STEP import | `0` | `0.266 s` | scoped STEP evidence |
| `attempt-03-tet10-mesh` | fresh-process second-order tetrahedral mesh | `0` | `0.297 s` | scoped mesh evidence |

All five owned child records report normal return or the retained nonzero return, no timeout, and owned-child cleanup verified. Exact source event times, PIDs, commands, raw streams, and hashes are in the provenance extract and attempt records; this report does not hand-enter a second event timeline.

## 6. OCC geometry and analytic checks / 幾何照合

The corrected create/export process observed three separate volume entities and an empty physical-group list. The independent analytic checks were:

| Primitive | OCC mass volume | Formula-derived volume | Relative error | Max bounding-box error |
|---|---:|---:|---:|---:|
| sphere | `14.137166941154069` | `14.137166941154069` | `0` | `1.00e-7 U` |
| oblique cylinder | `3.451039529968389` | `3.451039529968388` | `2.57e-16` | `1.00e-7 U` |
| rotated box | `4.284` | `4.284000000000001` | `2.07e-16` | `1.00e-7 U` |

The corrected geometry process produced the STEP artifact:

```text
bytes: 26300
SHA-256: DCBFF3A3CEF7EC796FF128E124AD32077BF1E3732878117C7DE62D1087349D83
```

The geometry is synthetic and independent. It does not establish any FEBio node, face, material, load, contact, or unit-mapping contract.

## 7. Fresh STEP reimport / STEP 再 import

The fresh Gmsh process reimported the generated STEP and observed three volume bodies. The sorted volume relative errors were `3.86e-16`, `2.29e-13`, and `0`. The reimport physical-group list was empty.

The exported STEP text contained explicit `SI_UNIT(.MILLI.,.METRE.)` length-unit records. This is an observation of this generated file's metadata only; it is not a product-wide unit contract or proof that every STEP producer/consumer preserves the same semantics.

## 8. Second-order tetrahedral mesh / Tet10

The fresh mesh process used the sphere primitive with `Mesh.ElementOrder=2`, complete second-order settings (`Mesh.SecondOrderIncomplete=0`, `Mesh.SecondOrderLinear=0`), `Mesh.Algorithm3D=1`, mesh sizes `0.55`/`0.8`, `Mesh.RandomFactor=0`, `Mesh.RandomSeed=1`, and `setOrder(2)`.

Gmsh returned element type `11`, name `Tetrahedron 10`, dimension `3`, order `2`, `10` nodes, and `4` primary nodes. The mesh contained `2,127` nodes and `1,222` Tet10 elements; no physical groups were present. Every checked Tet10 connectivity tuple had ten distinct node tags.

The returned reference coordinates independently identify four corner nodes and six midpoint nodes with relations `(0,1)`, `(1,2)`, `(0,2)`, `(0,3)`, `(2,3)`, and `(1,3)`. The minimum sampled `minDetJac` was `0.012991555090983535`; the minimum signed inverted condition number was `0.2614095200149805`. Both exceed the frozen positive threshold `1e-12`.

The earlier saved `physical_midpoint_deviation_first_element` values in the original Gmsh result are invalid and are explicitly superseded. The original helper built `node_map` with `flat_node_coords[index:index+3]` instead of `flat_node_coords[3*index:3*index+3]`; those six values were coordinate-indexing artifacts, not physical curved-element deviations. The original result file and native artifacts remain unchanged.

An independent, no-Gmsh, no-native reanalysis parsed the saved ASCII MSH 4.1 artifact directly:

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-gmsh-reanalysis-02\reanalysis-result.json
Input SHA-256: 740C794E8DBAF94C2B44901ED283CAD4F13832D3D629052AB153B3D30B29F5EE
Result SHA-256: 080AE4DD21B4E19D6E43F70F441C075BE54EB5F6F2CDF02EFC67054C9F165D6B
Provenance extract SHA-256: 637DB07B4CA00BE63FBBC43D3BD29E980199C3450B2E9EBDD631BC2BFBDF4350
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-gmsh-reanalysis-02\reanalysis-record.json
Record SHA-256: A16E92166D945480C448E3B0E1B0ECBEA9F218F652AFBEE98E52A93CB94A0160
```

The expanded child command recorded in that immutable record is:

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -X utf8 C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-gmsh-reanalysis-02\parse_saved_msh.py C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-gmsh-observation-01\attempt-03-tet10-mesh\sphere-tet10.msh
exit: 0
stdout SHA-256: 080AE4DD21B4E19D6E43F70F441C075BE54EB5F6F2CDF02EFC67054C9F165D6B
stderr bytes: 0; SHA-256: E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855
```

The reanalysis strictly parsed `2,127` nodes, `1,709` elements, and type-11 Tet10 count `1,222`; the first Tet10 node tags matched the saved native result record. The corrected six midpoint deviations are `6.280369834735101e-16`, `6.304854800243424e-16`, `8.881784197001252e-16`, `0`, `4.440892098500626e-16`, and `6.280369834735101e-16`, with maximum `8.881784197001252e-16`. All nodes remain within the sphere radius: maximum radius `1.5000000000000009` about center `(1.25, -2, 0.75)`. The direct Gmsh quality API fields are unaffected by this saved-file indexing bug; the Tet10 tuple is still not the FEBio Tet10 mapping, and no universal orientation, face conversion, or curved-element interoperability claim is made.

The mesh artifact is:

```text
bytes: 209861
SHA-256: 740C794E8DBAF94C2B44901ED283CAD4F13832D3D629052AB153B3D30B29F5EE
```

## 9. Checks and unverified boundaries / 検証境界

Completed for this documentation-only native observation:

```text
& 'C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe' -X utf8 scripts\scan_cae_data.py --root .   # PASS, exit 0
git diff --check                                                                                # exit 0
local Markdown-link audit                                                                       # exit 0
provenance raw-line/object/hash verification                                                    # PASS
```

Not run or not established by this observation:

- product `pytest`, Ruff, mypy, build, or installed-smoke gates;
- FEBio, official FBS, Studio GUI, solver, free-DOF, contact, or Hertz behavior;
- product-side Gmsh adapter, schema, readiness, mapping, or support claims;
- universal STEP unit semantics across exporters/importers;
- universal Tet10 orientation, node ordering, face conversion, or FEBio compatibility;
- real `02_CAE` data, real models, credentials, desktop state, and final BottomFrame E2E.

No push or integration was performed. The next step is independent exact review of the clean documentation commit.

## 10. Final bounded statement / 最終記録

`GMSH 4.15.2 NATIVE API INITIALIZED; 3 OCC BODIES VERIFIED; STEP ROUNDTRIP VERIFIED; TET10 MESH VERIFIED; PREFLIGHT ARITHMETIC CORRECTION RETAINED; PRODUCT/FEBIO/FBS/STUDIO/MAPPING/REAL-MODEL SUPPORT UNVERIFIED`
