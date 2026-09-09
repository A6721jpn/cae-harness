# P0-B Gmsh to FEBio Tet10 mapping observation

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

Date: 2026-09-07
Repository: `https://github.com/A6721jpn/cae-harness.git` (`REMOTE_CONFIGURED`)
Integration branch: `V2`
Worker branch: `codex/p0-b-gmsh-febio-mapping`
Clean base: `9c172eee38c13efd4c07da0e0699cc711a8d8cad`

## 1. Bounded result

> `P0-B SYNTHETIC GMSH->FEBio TET10 SOURCE-BACKED CANDIDATE MAPPING AND ALL-NODE-PRESCRIBED SI NEO-HOOKEAN OBSERVATION RECORDED; PRODUCT/GM-02/FB-01/FBS/XPLT-READER/STUDIO/FREE-DOF/REAL-MODEL COMPATIBILITY UNVERIFIED`

An independent ignored study generated a straight-sided `0.01 m` OCC cube through the task-local Gmsh 4.15.2 Python API, derived the FEBio Tet10 permutation from the actual Gmsh local coordinates and the primary FEBio quadratic-tetrahedron manual's numbered shape-function associations, validated connectivity and oriented boundary faces, compiled one SI-unit FEBio input, and ran one all-node-prescribed affine neo-Hookean patch through the installed FEBio 4.12.0 executable.

This is a mathematical native software observation. It is not a product adapter, full `GM-02`/`FB-01` acceptance, free-DOF equilibrium, contact, rigid-body, FBS, XPLT-reader, Studio, real-model, or `02_CAE` result.

The governing repository documents remain the [design specification](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md) and [greenfield plan](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md). This report does not amend them.

## 2. Evidence boundary and hashes

All new scripts, input, MSH, direct logs, solver log, XPLT, JSON records, and raw streams are ignored under:

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-gmsh-febio-mapping-01
```

The only tracked change from this study is this report. The recorded preflight and final FEBio launch records are:

| Evidence | SHA-256 |
|---|---|
| `preflight.json` | `85BEA31157EE59FC974BDC421E527B8BD5E2C7BF58EA9CE43F27920A849A86A5` |
| `preflight-correction-01.json` | `A561583177029CD16BDDEEEB36DDF87734BAD2A89F2C20B9FBAB5AE319CAF59E` |
| `preflight-correction-02.json` | `2C0307594DCD529FCC7DB7C4A0CCF190694444DEB59410B6B6E64585A550A515` |
| `preflight-correction-03.json` | `AC7ACA5B63882833F99E6C0D28670DB71125A45D7D1EBDDADA22441B455AFF3A` |
| `launch-preflight.json` | `7DEF14D15FCFFC14F309DE1D8D312E80079F849E9E5CB6A06467FB3A17DF78D3` |
| final Gmsh `cube-tet10.msh` | `42D60B90AE8F5DCB63CEB979499D90E72C1809CD948C7F177EDF2D313E6CFA3E` |
| final Gmsh `gmsh-result.json` | `68D6562672EFA271FB57132CDEE8FA4CF7E28192DAD188DAE81A9DD7393E4472` |
| FEBio input `cube-tet10.feb` | `BC384B72425D7F36EBC2A629F9D5FF7FA77DD8D6445DE0AF616E51A10507F7CD` |
| `mesh-and-reference.json` | `04422DCA0F46B1000844F797D59D862BE535C72EDA07430ABCC3E3A6CF300CEF` |
| `analysis.json` | `7A988089C08350F41BFD0751B93595F5220C977A961146A54D6FCCC262926E93` |
| `direct-output-record.json` | `790960DA5B3694E97A9CF1B964E97C5B64B2F3974357F5E9D5401C9D86071B3E` |

The direct FEBio files were resolved beside the absolute input by FEBio and were hashed after the successful owned run:

| File | Bytes | SHA-256 |
|---|---:|---|
| `prepared/node-data.txt` | 33882 | `6BEAACE2D0AF5E12C0FB551AB9F0231CDC606CDDF59197DD8C35FD2A81F4F5F7` |
| `prepared/element-data.txt` | 13796 | `A534DF7BED29E62FFF2C194DECB8FCC865D835485F0A3DA7952EDE9FF9D529D0` |
| `attempt-05-febio/febio.log` | 9801 | `D78DE75F79FEAD5CCE273506AF35E23EACB934BC42F25E2803467445AFA3E665` |
| `attempt-05-febio/febio.xplt` | 39815 | `54226EA0CF7701D8DD662197DD3E062D7DEF6295E1F1DAB33437F2C7D2B7D69E` |

The original evidence did not meet the work-order read-only-protection procedure. A post-run filesystem audit found `0` of `61` files under the study root carrying the Windows `ReadOnly` attribute, including `preflight.json`, `launch-preflight.json`, `prepared/cube-tet10.feb`, the original helper scripts, and the attempt records. A static audit of the retained helper and record files found no `chmod`, `attrib`, or equivalent read-only operation. These hashes establish byte identity at their capture points; they do not establish write protection or immutability. No evidence here shows that the input changed or that the native run failed for this reason. This correction does not alter attributes retroactively and makes no historical read-only-compliance claim.

## 3. Preflight and source-backed mapping

The working convention is explicitly SI: coordinates and length are metres, stress and energy density are pascals, force is newtons, and energy is joules. The synthetic geometry is `OCC addBox(0,0,0,L,L,L)` with `L=0.01`, origin `(0,0,0)`, and no STEP/default-unit dependency.

The native identities captured before launch were:

| Component | Identity |
|---|---|
| Gmsh Python | 4.15.2; task-local `gmsh.py` SHA `A56EBE69DC57A3EA15EEE191CAE4F1881B06784174B2D9DF306A98BDD8B06606` |
| Gmsh native DLL | `gmsh-4.15.dll`, SHA `6CAC3EEFB477265D9FA60BBD869DBBBF7C7CA4CB308C0F8F2B43E91D43DE3C1C` |
| Gmsh wheel lock | SHA `7B36083BB410FA27C5D0E052929D1A9844A5B09169D66017B72B41AABD49D711` |
| FEBio executable | `C:\Program Files\FEBioStudio\bin\febio4.exe`, SHA `03B9DB12C4B3E2ED0CF027BE6B8B5D0CEF2D4EDC9EAB26F5A2BD8193EFB770C9` |
| FEBio version | `4.12.0`, observed in native stdout |

The Gmsh mesh settings were `MeshSizeMin=MeshSizeMax=0.005 m`, 3D algorithm `1`, order `2`, `SecondOrderIncomplete=0`, and `SecondOrderLinear=1`. The final mesh had `231` nodes and `100` type-11 Tet10 elements; the type-count record was exactly `{"11":100}`.

The primary sources used for the independent mapping record are the [Gmsh reference manual](https://gmsh.info/doc/texinfo/gmsh.html) and the [FEBio Theory Manual, quadratic tetrahedral elements](https://help.febio.org/docs/FEBioTheory-4-7/TM47-Subsection-4.1.4.html). The FEBio page states that the element has four corner nodes and six edge-midpoint nodes. Its rendered area-coordinate definitions are `t1=1-r-s-t`, `t2=r`, `t3=s`, and `t4=t`; its numbered shape functions are `H_i=t_i(2t_i-1)` for `i=1..4`, followed by `H5=4t1t2`, `H6=4t2t3`, `H7=4t3t1`, `H8=4t1t4`, `H9=4t2t4`, and `H10=4t3t4`. Those numbered shape functions establish the candidate FEBio edge order `(1-2, 2-3, 3-1, 1-4, 2-4, 3-4)`. The 11-point Gauss-Lobatto table is an integration-rule table and is not used as connectivity-node-order proof. The exact source/event extraction for this correction is retained in ignored `P0B-gmsh-febio-mapping-correction-02/source-audit.json` with SHA-256 `E9579131DA19E0616209BAF2C99C8EFCDB4E01A58685679250BAF039EFD6CF7C`. The separately authored [new-V2 neo-Hookean observation](2026-09-07-p0-b-neo-hookean-observation.md) supplies an additional native observation of this supported candidate order; its mesh was not copied into this study.

The actual Gmsh type-11 local coordinates were:

```text
(0,0,0), (1,0,0), (0,1,0), (0,0,1),
(0.5,0,0), (0.5,0.5,0), (0,0.5,0), (0,0,0.5),
(0,0.5,0.5), (0.5,0,0.5)
```

Therefore the explicit candidate position permutation used to emit FEBio order was:

```text
FEBio position <- Gmsh position: [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]
```

The last two Gmsh midside positions are exchanged relative to the FEBio edge order above. The mapping was derived from coordinates, not from node numbers or nearest-neighbour inference.

Validation results before FEBio launch:

- all `100/100` mapped elements had ten distinct existing node tags;
- all six midside relations passed with maximum error `1.939479807224432e-18 m` against the recorded `1e-12 m` bound;
- all corner determinants were positive, with minimum `3.124999999999998e-08 m^3` against the recorded `1e-18 m^3` bound;
- a negative in-memory mutation that swapped two mapped corner entries was rejected with five validator errors; `solver_launched_for_mutation=false`;
- full incidence contained `158` internal faces and `84` boundary triangles, with `14` triangles on each named cube face;
- each named face area was `0.0001 m^2`; face node groups were nonempty with `37` nodes each and the body group contained all `231` nodes;
- the intentionally nonexistent physical group `2/999999` was recorded as `missing`, with the native API error preserved and `fallback_used=false`.

The first three Gmsh attempts are retained as failures. After attempt-01 observed about `1e-7 m` of Gmsh bounding-box enclosure padding, its face-label classifier criterion of `1e-10 m` was changed only for face classification to `max(L*1e-8, 1e-6 m)` before the next native attempt. The midpoint `1e-12 m`, boundary-area relative `1e-9`, and positive-corner determinant `1e-18 m^3` acceptance criteria were unchanged; no claim is made that all thresholds were unchanged. The correction records also document the installed two-value `getNodesForPhysicalGroup` return shape and the expected nonexistent-group exception. No geometry, mesh setting, mapping, analytic reference, executable, or native-attempt budget was changed.

## 4. FEBio input and analytic reference

The input is a single static, monotonic `0 -> 1` load-controller step with all mesh nodes prescribed by `F=diag(0.999,1,1)`. It uses `neo-Hookean`, `E=1e6 Pa`, `nu=0.3`, no density, no contact, no rigid body, and no free degrees of freedom.

The independently computed reference uses:

```text
mu      = E / (2*(1+nu))
lambda  = nu*E / ((1+nu)*(1-2*nu))
W       = mu/2*(I1-3) - mu*ln(J) + lambda/2*ln(J)^2
sigma   = (1/J)*(mu*(B-I) + lambda*ln(J)*I)
```

For the prescribed field, `J=0.999`, `I1=2.998001`, and:

```text
W = 0.6734939506222787 Pa
sigma = diag(-1347.4052900497707,
             -577.7895204340116,
             -577.7895204340116) Pa
```

The expected face-normal forces, using initial/current areas from the stated deformation, were `0.13474052900497707 N` on `x0`, `-0.13474052900497707 N` on `xL`, and `±0.05772117309135776 N` on each `y`/`z` face. Only the face-normal component was summed; shared edge/corner nodes were not used to claim complete traction vectors.

## 5. Native process ledger

Every native child had a unique output directory, a direct owned PID, Windows creation time, raw stdout/stderr, exit code, and output hashes. The configured environment thread limits were `OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`, `OPENBLAS_NUM_THREADS=2`, and `NUMEXPR_NUM_THREADS=2`. No descendant enumeration or actual-thread-count claim is made. Each timeout bound was `120 s`; five of six allowed native attempts were consumed.

| Attempt | Kind | PID / creation UTC | Duration (s) | Exit | Result |
|---|---|---|---:|---:|---|
| `attempt-01-gmsh` | Gmsh | `42536` / `2026-09-07T08:10:44.535094Z` | `0.313` | `1` | classifier rejected Gmsh bbox padding; stderr SHA `A45048A72853D3CC4C9BF66D1B7E490BBC512861A9431AF52C3B5F3A09CE7535` |
| `attempt-02-gmsh-corrected` | Gmsh | `48364` / `2026-09-07T08:11:49.345242Z` | `0.281` | `1` | MSH written, metadata capture hit two-value API return; MSH SHA `42D60B90AE8F5DCB63CEB979499D90E72C1809CD948C7F177EDF2D313E6CFA3E` |
| `attempt-03-gmsh-final` | Gmsh | `40020` / `2026-09-07T08:12:41.553948Z` | `0.281` | `1` | MSH written, missing-group exception not yet captured; MSH SHA `42D60B90AE8F5DCB63CEB979499D90E72C1809CD948C7F177EDF2D313E6CFA3E` |
| `attempt-04-gmsh-final` | Gmsh | `428` / `2026-09-07T08:13:38.024696Z` | `0.282` | `0` | final mesh/result; stdout SHA `3494AF6631BF039659F3FDB173E8619C37DEB886D312B69552A5E2A9B1D51005` |
| `attempt-05-febio` | FEBio | `27592` / `2026-09-07T08:15:47.150278Z` | `0.172` | `0` | normal termination; stdout SHA `5D31A15285781792C08C17DD1B3BF58A8E3629410213CCD2D612FF91C4BDE27B` |

The final FEBio argv was:

```text
C:\Program Files\FEBioStudio\bin\febio4.exe
  -i C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-gmsh-febio-mapping-01\prepared\cube-tet10.feb
  -o C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-gmsh-febio-mapping-01\attempt-05-febio\febio.log
  -p C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-gmsh-febio-mapping-01\attempt-05-febio\febio.xplt
  -noappend -noconfig
```

## 6. Direct numeric observation

The direct requested node and element files contained both state `0` and final state `1`, with exact node/element ID sets (`231` nodes and `100` elements). The final state passed every recorded observation criterion:

| Metric | Observation |
|---|---:|
| Maximum prescribed displacement error | `8.470329472543003e-21 m` |
| Maximum current-coordinate error | `4.999906738634152e-15 m` |
| Maximum nonzero normal-stress absolute residual | `1.0115854820469394e-09 Pa` |
| Maximum zero-stress/shear absolute residual | `4.05332492671e-10 Pa` |
| Maximum face-normal relative residual | `2.821728249656877e-10` |
| Maximum global reaction-sum component | `2.26683548734751e-14 N` |
| Final direct-log state / observed end time | `1 / 1.0` |
| Normal termination | observed |

The direct logfile `sed` values had a maximum numeric relative difference of `8.718914409358172e-11` from the reference-volume `W`, but the density/volume convention for that direct field is not independently resolved here. This is a numeric diagnostic, not a semantic energy acceptance.

The [FEBio neo-Hookean feature manual](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_neo-hookean/), [plot-variable reference](https://febiosoftware.github.io/febio-feature-manual/plotvars/), and [logfile output syntax](https://help.febio.org/docs/FEBioUser-4-9/UM49-3.19.1.3.html) support the material/output vocabulary. They do not establish product adapter support or the exact convention of every direct logfile quantity across builds.

## 7. Exact commands and gates

The bounded commands were run from the repository root with the absolute Python 3.12 interpreter:

```powershell
& 'C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe' -X utf8 .local\verification\P0B-gmsh-febio-mapping-01\prepare_preflight.py
# exit 0; PREFLIGHT_FROZEN (record label; no read-only claim)

& 'C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe' -X utf8 .local\verification\P0B-gmsh-febio-mapping-01\build_febio.py
# exit 0; 231 nodes / 100 Tet10; negative mutation rejected

& 'C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe' -X utf8 .local\verification\P0B-gmsh-febio-mapping-01\freeze_launch.py
# exit 0; FEBIO_LAUNCH_PREFLIGHT_FROZEN (record label; no read-only claim)

& 'C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe' -X utf8 .local\verification\P0B-gmsh-febio-mapping-01\record_direct_outputs.py
# exit 0; DIRECT_OUTPUTS_HASHED

& 'C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe' -X utf8 .local\verification\P0B-gmsh-febio-mapping-01\analyze_febio.py
# exit 0; all recorded observation criteria true
```

The owned-native wrapper was used for every Gmsh/FEBio child with `--timeout-seconds 120`, unique output directories, and raw stream capture. Product `pytest`, Ruff, mypy, build, installed smoke, official FBS, Studio, and real-model gates were not run because this is a documentation-only bounded native observation; those gates remain unverified, not passed.

## 8. Unverified boundaries

- No Gmsh-to-product adapter or FEBio input adapter was changed or accepted.
- The manual shape-function provenance supports the stated candidate Tet10 order; the native affine patch only corroborates bounded behavior and does not establish general compatibility.
- The successful input was all-node-prescribed; it does not establish free-DOF equilibrium, support semantics, contact, rigid-body semantics, or solver robustness.
- XPLT was generated and hashed only. No independent reader, FBS, compression, dictionary/state, or variable-semantic acceptance was performed.
- FEBio Studio was not launched. No GUI, final-state viewer, or post-processing confirmation was performed.
- No production material/profile claim, real CAD/model claim, `02_CAE` access, BottomFrame E2E, P0/P3/E2E gate, or product-ready declaration follows from this observation.
- Historical read-only protection was not demonstrated; the retained hashes are content records, not immutable or OS-read-only guarantees.
- No push or integration was performed. The next step is independent read-only review of this exact clean commit plus the ignored evidence hashes.
