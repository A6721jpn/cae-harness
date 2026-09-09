# P0-B native free-DOF compressible neo-Hookean observation

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

Date: 2026-09-07
Repository: `https://github.com/A6721jpn/cae-harness.git` (`REMOTE_CONFIGURED`)
Integration branch: `V2`
Worker branch: `codex/p0-b-free-dof-patch`
Clean base: `9f361893b7966f583dc8b06e1c69e70baa9ae6c1`

## 1. Bounded result

> **P0-B SYNTHETIC FREE-DOF COMPRESSIBLE NEO-HOOKEAN OBSERVATION RECORDED; THE LIMITED NATIVE INPUT PASSED ITS FROZEN NUMERIC GATES; PRODUCT ADAPTER, FB-01/02/03, FBS, XPLT-READER, STUDIO, REAL-MODEL, AND `02_CAE` COMPATIBILITY REMAIN UNVERIFIED**

This study copied only the accepted same-V2 Tet10 mesh into a fresh ignored directory, revalidated the mesh and the emitted FEBio XML, and ran a synthetic SI-unit cube through the installed FEBio 4.12.0 executable with only four normal-component prescribed-displacement constraints. The final native attempt returned process exit `0`, emitted normal termination, produced 11 direct-log states through time `1.0`, and passed the independently computed compressible neo-Hookean reference checks.

This is bounded synthetic/native evidence. It is not a product adapter test, a full `FB-01`/`FB-02`/`FB-03` acceptance, official FBS evidence, an XPLT-reader acceptance, a FEBio Studio observation, a real `02_CAE` result, or a BottomFrame result. The governing [design specification](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md) and [greenfield plan](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md) are unchanged.

## 2. Evidence boundary and artifact identity

All new scripts, inputs, mesh copies, direct outputs, raw streams, hashes, and JSON records are ignored under:

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-free-dof-neo-hookean-01
```

The only tracked change from this study is this Markdown report. No real CAE data, credentials, solver configuration, installed binary, or product source was modified.

| Evidence | Bytes | SHA-256 |
|---|---:|---|
| copied accepted `source-cube-tet10.msh` | 20191 | `42D60B90AE8F5DCB63CEB979499D90E72C1809CD948C7F177EDF2D313E6CFA3E` |
| `prepared-v2/template-free-dof.feb` | 29377 | `BDB5442CF3BAD270A025C917AB92E5C076FE11E9C28E201A942A0C5BF5514363` |
| `prepared-v2/mesh-oracle.json` | — | `7DE207D7F89BC948E3551ABA44C0DEC9844C24698520D742281E54321461379C` |
| `prepared-v2/oracle.json` | — | `68C35C1309FD7BA4945C9CF302FDE7FA929C22ADBB843EE828882726239E095E` |
| `prepared-v2/oracle-high-precision.json` | — | `7551C2655188E9164925BD55842658E63A6F4E453B8FEA363D88CF1A34A893A6` |
| `negative/negative-probe-record.json` | — | `E7FF95E489EC1C44EDFA53D0B3672F37344981210E69AE19A327E121F49460A7` |
| `attempt-01-febio/attempt-record.json` | — | `0BB6F6938E89FB281F5421FF3D6270D37CF8E2C4C0EC8A43521E3B08D6B6FA4F` |
| `attempt-02-febio/launch-preflight.json` | — | `011DC73D220AAA1882654973CF430ADEF7639E7B80205628BC8E5528B18DA93B` |
| `attempt-02-febio/attempt-record.json` | — | `4E0DDAC97CF1C3A51D016B4858F3D7771FD3C13AD49A494A37DC9B6455BD0F6F` |
| `analysis.json` (attempt 01, failed observation) | — | `AF4F5CBA49CF76E6604A58B299E9CB6CBCD2C38133A37C4DE21A91852B3CC51C` |
| `analysis-02.json` (attempt 02, accepted observation) | — | `B33FF7F13CD07F5CCC96C132253CDD1CD3570E07A1C132BF1129F5F5A64E59E7` |
| `final-audit.json` | — | `3D62C4E0E03C7B9B7BCC10890374591234DCD80DB4C734506672E02DEB6B09A4` |
| `freeze-verification-final.json` | — | `592FC8DD031B34F5763C1868B05B13C3DC02157046E1B48F58F866D764BD1172` |

The installed native solver was `C:\Program Files\FEBioStudio\bin\febio4.exe`, observed as FEBio `4.12.0` in the native stdout/log, with SHA-256 `03B9DB12C4B3E2ED0CF027BE6B8B5D0CEF2D4EDC9EAB26F5A2BD8193EFB770C9`. The executable was read and executed only; its attributes and contents were not changed.

## 3. Mesh and Tet10 revalidation

The copied mesh is the accepted same-V2 source identified above; no prior FEBio input or prior result was copied. The fresh parser read MSH 4.1 and found `231` nodes, `216` total MSH elements, and `100` type-11 Tet10 elements. The fixed candidate conversion was:

```text
FEBio position <- Gmsh position: [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]
```

For each mapped element, the study checked ten distinct existing node tags, all six edge-midpoint relations, and the positive corner tetrahedron determinant. Results were:

| Check | Observation |
|---|---:|
| Nodes / type-11 Tet10 elements | `231 / 100` |
| Maximum midpoint error | `1.939479807224432e-18 m` |
| Minimum / maximum positive corner volume | `5.20833333333333e-09 / 1.86080932617188e-08 m^3` |
| Boundary triangles | `84` total; `14` on each of six cube faces |
| Face node groups | `37` nodes on each named face |
| Internal faces | `158` (derived from the `400` tetrahedral face incidences and `84` boundary faces) |
| Coordinate bounds | `[0, 0.01]^3 m` |

Two offline connectivity mutations were intentionally rejected before any solver launch: swapping a midside pair returned exit `1` with `Tet10 midpoint error exceeds 1e-12 m`; an orientation-preserving midside remap with an inverted corner order returned exit `1` with `non-positive Tet10 determinant`. The first negative-probe helper invocation was itself a failed setup run because the inverted-corner child did not produce the expected error fragment; a later corrected invocation returned exit `0` because both negative child validations failed as required. No mutated input was sent to FEBio.

## 4. Free-DOF contract, material, and analytic reference

The accepted attempt-02 XML preflight found `693` total displacement DOFs, `148` constrained node-component pairs, and `545` free DOFs. There were `61` wholly free interior nodes (`171` through `231` in the emitted node IDs). These `545`/`61` values qualify attempt 02 only: attempt 01 passed a digit-token preflight but its wrapped native NodeSet serialization did not preserve the same effective component selections. The only intended displacement boundary conditions were:

| Node set | Prescribed component | Value at load factor `s` |
|---|---|---:|
| `x0` | `ux` | `0` |
| `xL` | `ux` | `-1e-5*s m` |
| `y0` | `uy` | `0` |
| `z0` | `uz` | `0` |

Every other component remained free. The material was the FEBio `neo-Hookean` type with `E=1e6 Pa`, `nu=0.3`, ten equal static steps from `s=0` to `s=1`, and no contact, rigid body, or prescribed tangential components. The XML omitted density; the FEBio 4.12 log reported runtime default `density=1`, which is relevant here only within the static, no-body-force scope. The [official neo-Hookean feature reference](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_neo-hookean/) supplies the material vocabulary and the `E`/`v` parameterization; that page identifies itself as FEBio 4.13, while this native run was FEBio 4.12.0, so it does not establish cross-version or product compatibility. The [official prescribed-displacement reference](https://febiosoftware.github.io/febio-feature-manual/features/solid_bc_prescribed_displacement/) supplies the node-set/DOF/value vocabulary; zero-valued prescribed displacement was used so the native reaction output was requested on those support components.

The accepted attempt-02 FEBio log recorded `analysis=STATIC`, `time_steps=10`, `step_size=0.1`, `plot_zero_state=yes`, `plot_level=PLOT_MAJOR_ITRS`, `output_level=OUTPUT_MAJOR_ITRS`, `plot_stride=1`, `output_stride=1`, and no automatic time stepper. Its solver profile recorded `dtol=0.001`, `etol=0.01`, `rtol=0`, `min_residual=1e-20`, `max_refs=50`, `reform_each_time_step=yes`, `diverge_reform=yes`, and Pardiso. Integration choices and other FEBio defaults not exposed by this input/log pair remain unverified; no such default is inferred here.

The independent reference used:

```text
mu     = E / (2*(1+nu))
lambda = nu*E / ((1+nu)*(1-2*nu))
W      = mu/2*(I1-3) - mu*ln(J) + lambda/2*ln(J)^2
F      = diag(a,b,b)
J      = a*b^2
```

At the final `a=0.999`, `b` is the positive root of `mu*(b^2-1)+lambda*ln(a*b^2)=0`. The frozen floating-point oracle recorded `b=1.0003001591042167`, `sigma_xx=-1000.17268579658 Pa`, `xL=-0.100077319778249 N`, and `x0=+0.100077319778249 N`. On every nonzero step its bisection reached the `200`-iteration cap; at the final step the residual was `3.049365204788046e-10 Pa`, above its declared `1e-13 Pa` stopping threshold, and the script emitted no convergence flag. Therefore this report does not call the frozen float oracle converged. A post-run 80-digit Decimal cross-check found `b=1.000300159104216465...`, residual `-1.0907e-73 Pa`, and a force difference of only `2.09e-14 N` from the frozen float oracle. The reported native gates use the frozen oracle; the high-precision result is a numerical cross-check, not a post-hoc tolerance change or a claim of native solver convergence.

The force expression uses the current deformed x-face area: `sigma_xx*b^2*L^2`. Equivalently, with `P=J*sigma*F^-T`, `F=diag(a,b,b)`, and `J=a*b^2`, `Pxx=b^2*sigma_xx`, so the reference-area form `Pxx*L^2` is identical. This is an area/traction identity, not an additional solver assumption.

Frozen thresholds, set before native output was collected, were:

| Metric | Threshold |
|---|---:|
| Axial `sigma_xx` relative error | `1e-3` |
| `x0`/`xL` face-force relative error | `1e-3` |
| Nonzero displacement-component relative error | `1e-3` |
| Zero displacement-component absolute error | `1e-9 m` |
| Transverse/shear stress absolute magnitude | `1 Pa` |
| Global reaction-vector max component | `1e-7 N` |

## 5. Native attempt ledger

Both FEBio attempts used unique directories, absolute input/output paths, `-noappend -noconfig`, a `120 s` per-attempt bound, and environment thread limits `OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`, `OPENBLAS_NUM_THREADS=2`, and `NUMEXPR_NUM_THREADS=2`. The wrapper observed only the owned direct child; it does not claim descendant enumeration or actual thread count. It recorded parent-clock launch intervals and child PIDs, but did not capture an OS process-creation timestamp.

| Attempt | Process exit | Normal termination | Direct final rows | Result |
|---|---:|---|---:|---|
| `attempt-01-febio` | `0` | yes | `219` nodes / `95` elements | **failed observation**: the wrapped serialization affected all five NodeSet lists and both logfile-selection lists; effective native constraints and output IDs were incomplete |
| `attempt-02-febio` | `0` | yes | `231` nodes / `100` elements | accepted bounded observation |

Attempt 01 is intentionally not presented as success. Its solver completed normally, but its wrapped comma-separated serialization affected all five NodeSet lists as well as both logfile-selection lists. The effective native constraints therefore differed from the intended lists: for example, node `65` on `x0` reported `ux=-1.6279045917e-6 m` instead of zero, and node `86` on `xL` reported `ux=-8.40359765413e-6 m` instead of `-1e-5 m`; nodes `47`, `68`, `89`, `107`, `131`, and `149` showed the analogous first-continuation-ID problem on other prescribed components. The retained `analysis.json` rejects complete IDs, displacement, stress, and reaction gates, and exits `1`. The fresh attempt-02 template repaired the shared NodeSet/logfile serialization in a new input; it retained the same mesh, intended material/BC contract, analytic reference, executable, and frozen numeric thresholds. The `545` free-DOF / `61` wholly-free-node observation is therefore qualified to attempt 02, not retroactively assigned to attempt 01.

The accepted attempt’s exact argv was:

```text
C:\Program Files\FEBioStudio\bin\febio4.exe
  -i C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-free-dof-neo-hookean-01\attempt-02-febio\free-dof.feb
  -o C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-free-dof-neo-hookean-01\attempt-02-febio\febio.log
  -p C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-free-dof-neo-hookean-01\attempt-02-febio\febio.xplt
  -noappend -noconfig
```

## 6. Accepted native numeric observation

The accepted direct node and element files each contained states `0, 0.1, ..., 1.0`. The final block was at time `1.0`, finite, and had the complete expected ID sets. The final measurements were:

| Metric | Observation |
|---|---:|
| Maximum `ux`/`uy`/`uz` relative displacement error | `1.5736035601e-09 / 1.5409596941e-09 / 2.1859201794e-09` |
| Maximum zero-component displacement absolute error | `0 m` for each component in the direct output |
| Maximum axial stress relative error | `8.2327818212e-10` |
| `sigma_xx` range across 100 elements | `-1000.17268662` to `-1000.17268509 Pa` |
| Maximum transverse/shear component magnitude | `4.83308053134e-07 Pa` |
| `x0` reaction force relative error | `7.3737429162e-11` |
| `xL` reaction force relative error | `3.0860877648e-11` |
| Global reaction-sum maximum component | `1.0467912127e-11 N` |
| `y0` summed `Ry` / `z0` summed `Rz` | `9.6702429233e-12 / 9.6677819005e-12 N` |
| Direct final state / observed time | `10 / 1.0` |

The accepted native output artifacts were:

| File | Bytes | SHA-256 |
|---|---:|---|
| `attempt-02-febio/stdout.raw` | 20442 | `6AD5D914383160A88C93BE64E2A762867C9CAC7A5560529EFD81FA3203D5AC6F` |
| `attempt-02-febio/stderr.raw` | 0 | `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855` |
| `attempt-02-febio/febio.log` | 26667 | `98AF5A9F0BA51983CD294E3DAA8D534C47194D8DE51205EED6583BDC5B1F8BE2` |
| `attempt-02-febio/febio.xplt` | 151753 | `A096522BBDF8FFB4EE9A01C23E57494CAA8EF9D14D606D240CC5CCE626567AE8` |
| `attempt-02-febio/node-data.txt` | 255019 | `578A08D601B40F3CD2A491879343D6A2675E04CA30F09771AD30369C627D6A6D` |
| `attempt-02-febio/element-data.txt` | 129243 | `6621902170258BEBD6957045420A2454F8E4757C942CAF4A7A5967A9107CFF6D` |

The XPLT is presence- and hash-recorded only. No XPLT reader, dictionary/state validator, FBS authority check, stale/truncation mutation probe, or Studio load was used. The direct `sed` field was retained as raw evidence but was not made an acceptance gate because its density/volume convention was not independently fixed here.

## 7. Immutability and prior-study preservation

The launch inputs and pre-launch evidence were frozen with the Windows `ReadOnly` attribute and hashed. This is accidental-write protection, not OS immutability or a security/ownership protocol. The historical freeze snapshots covered `25` files before attempt 01, `44` files before attempt 02, and `60` files in the final sweep. Their recorded comparisons report zero missing files, size mismatches, or hash mismatches, and every file in each recorded set was `ReadOnly` when checked. The two final verification records were created after the 60-file capture, so that count is not a claim that every later-created record was frozen.

The chronology does not satisfy the stronger immediate-verification requirement. The 25-file capture was at `2026-09-07T09:07:55.400681+00:00`, followed by attempt-01's parent-clock interval `2026-09-07T09:08:06.766509+00:00` to `2026-09-07T09:08:07.032509+00:00`; the 44-file capture was at `2026-09-07T09:11:05.453423+00:00`, followed by attempt-02's parent-clock interval `2026-09-07T09:11:14.662923+00:00` to `2026-09-07T09:11:14.938924+00:00`. The freezer captured current hashes and attributes but did not compare every file against an earlier expected hash. The launcher checked the input `ReadOnly` bit and four output destinations for absence, not all expected frozen hashes. The first full expected-hash/attribute comparison was after both launches (`freeze-verification.json`, captured at `2026-09-07T09:14:07.362791+00:00`). The attempt records' before/after `ReadOnly` fields are payload fields assembled around the run; a separate pre-launch input guard exists, but those fields are not independent temporal samples. No OS process-creation timestamp was captured; the stored PID and parent-clock timestamps must not be promoted to one.

The correction-02 audit under `.local/verification/P0B-free-dof-neo-hookean-correction-02/correction-02-audit.json` captured both the retained original study and correction-01 before and after this report edit and found zero byte, hash, size, or Windows-attribute mismatches. The before/after prior-study manifest compared `3,190` files across the ten existing ignored verification studies, including content hashes and Windows attributes. That comparison also passed exactly; prior ignored studies were not modified. The source mesh copy retained the accepted SHA-256 shown above.

## 8. Exact commands and gate results

The orchestration commands were run from the repository root with Python 3.12 and absolute paths inside the ignored study root; each native child cwd is retained in its attempt record. The exact original command inputs and outputs, source event IDs, source ordinals, timestamps/time kinds, retained native argv/cwd records, counts, and exits are preserved in the ignored `.local/verification/P0B-free-dof-neo-hookean-correction-02/original-provenance-correction-02.json` manifest. It contains `24` selected original command/output pairs from the source session archive; the lines below identify the relevant stages without replacing the full records. The failed negative-probe context is parsed from its own `all_passed=false` output, while the later successful negative probe is separately bound to its own event pair:

```text
event 7259 -> 7262  prepare_free_dof.py first run                 exit 1; helper setup failure: node coordinate outside cube bounds
event 7280 -> 7283  prepare_free_dof.py second run                exit 1; helper setup failure: unexpected boundary triangle count 84
event 7335 -> 7338  prepare_free_dof.py corrected run             exit 0; 231 nodes, 100 Tet10, 545 free DOFs
event 7342 -> 7345  negative_probes.py initial run                wrapper exit 1; child exits invert_corner=1 and swap_midpoint=1, but the expected invert-corner fragment was not observed (not a successful negative gate)
event 7358 -> 7361  negative_probes.py corrected run              wrapper exit 0; both expected child validators exit 1
event 7405 -> 7408  prepare_attempt.py attempt 01                 exit 0; 545 free DOFs, outputs absent
event 7412 -> 7415  helper py_compile before attempt 01           compile exit 0
event 7419 -> 7422  freeze_launch.py before attempt 01           exit 0; current-hash/attribute capture, 25 files
event 7426 -> 7429  run_native_attempt.py attempt 01              wrapper exit 0; FEBio child exit 0, normal termination, four solver artifacts
event 7433 -> 7436  analyze_native_attempt.py attempt 01          analysis exit 1; original helper KeyError, no accepted analysis output
event 7463 -> 7466  analyze_native_attempt_v2.py attempt 01       analysis exit 1; retained `analysis.json`, four failed behavioral gates, exact output hash recorded
event 7502 -> 7505  prepare_free_dof_v2.py                      exit 0; 231 nodes, 100 Tet10, 545 XML-token free DOFs
event 7509 -> 7512  prepare_attempt.py attempt 02                 exit 0; 545 free DOFs, outputs absent
event 7516 -> 7519  py_compile plus freeze_launch.py              compile exit 1, freeze exit 1; ReadOnly pyc replacement failure
event 7523 -> 7526  ReadOnly pycache cleanup                      tool completed; setup cleanup before the later syntax check (no explicit child exit marker)
event 7530 -> 7533  syntax check plus freeze_launch.py             syntax exit 0, freeze exit 0; 44-file capture
event 7537 -> 7540  run_native_attempt.py attempt 02              wrapper exit 0; FEBio child exit 0, normal termination, four solver artifacts
event 7544 -> 7547  analyze_native_attempt_v2.py attempt 02       analysis exit 0; all numeric gates true
event 7553 -> 7556  inline 80-digit Decimal check                 tool completed; high-precision cross-check printed (no explicit child exit marker)
event 7571 -> 7574  oracle_high_precision.py                      exit 0; recorded residual and force difference
event 7585 -> 7588  capture_prior_manifest.py                     exit 0; 3190 prior files captured
event 7599 -> 7602  verify_freeze.py                              verify exit 0; first full 25/44 hash comparison after both launches
event 7606 -> 7609  final_audit.py                                exit 0; preservation and attempt checks true
event 7615 -> 7618  final freeze plus verification                freeze/verify exit 0; historical 60-file final sweep
```

The repository product gates were not run for this documentation-only native observation: `python -m pytest`, Ruff format/check, mypy, build, installed smoke, official FBS, Studio, real-model E2E, and BottomFrame E2E remain unverified. They must not be inferred from this synthetic result.

## 9. Unverified boundaries and next task

- No product Gmsh/FEBio adapter, schema, CLI, solver ownership implementation, or reader was changed or accepted.
- The native result supports only this SI cube, this Tet10 input, this material parameterization, these four normal-component BCs, and this small compression. It does not establish general free-DOF, support, contact, rigid-body, or nonlinear solver robustness.
- The XPLT was generated and hashed only; no official FBS, XPLT-reader, compression, state, or variable-semantic claim follows.
- FEBio Studio was not launched. No GUI/post-processing or final-state display evidence exists.
- No real `02_CAE` data, credentials, real CAD/model, BottomFrame, official external FBS, or product release gate was accessed.
- No push or V2 integration was performed. The next task is an independent read-only review of this exact clean docs-only commit and the ignored evidence hashes.
