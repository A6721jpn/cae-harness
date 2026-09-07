# P0-B native free-DOF compressible neo-Hookean observation

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
| `prepared-v2/template-free-dof.feb` | 29562 | `BDB5442CF3BAD270A025C917AB92E5C076FE11E9C28E201A942A0C5BF5514363` |
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

Two offline connectivity mutations were intentionally rejected before any solver launch: swapping a midside pair returned exit `1` with `Tet10 midpoint error exceeds 1e-12 m`; an orientation-preserving midside remap with an inverted corner order returned exit `1` with `non-positive Tet10 determinant`. The wrapper’s expected-negative-probe command returned exit `0` because both negative child validations failed as required. No mutated input was sent to FEBio.

## 4. Free-DOF contract, material, and analytic reference

The fresh XML preflight found `693` total displacement DOFs, `148` constrained node-component pairs, and `545` free DOFs. There were `61` wholly free interior nodes (`171` through `231` in the emitted node IDs). The only displacement boundary conditions were:

| Node set | Prescribed component | Value at load factor `s` |
|---|---|---:|
| `x0` | `ux` | `0` |
| `xL` | `ux` | `-1e-5*s m` |
| `y0` | `uy` | `0` |
| `z0` | `uz` | `0` |

Every other component remained free. The material was the FEBio `neo-Hookean` type with `E=1e6 Pa`, `nu=0.3`, ten equal static steps from `s=0` to `s=1`, and no density, contact, rigid body, or prescribed tangential components. The [official neo-Hookean feature reference](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_neo-hookean/) supplies the material vocabulary and the `E`/`v` parameterization. The [official prescribed-displacement reference](https://febiosoftware.github.io/febio-feature-manual/features/solid_bc_prescribed_displacement/) supplies the node-set/DOF/value vocabulary; zero-valued prescribed displacement was used so the native reaction output was requested on those support components.

The independent reference used:

```text
mu     = E / (2*(1+nu))
lambda = nu*E / ((1+nu)*(1-2*nu))
W      = mu/2*(I1-3) - mu*ln(J) + lambda/2*ln(J)^2
F      = diag(a,b,b)
J      = a*b^2
```

At the final `a=0.999`, `b` is the positive root of `mu*(b^2-1)+lambda*ln(a*b^2)=0`. The frozen floating-point oracle recorded `b=1.0003001591042167`, `sigma_xx=-1000.17268579658 Pa`, `xL=-0.100077319778249 N`, and `x0=+0.100077319778249 N`. A post-run 80-digit Decimal cross-check found `b=1.000300159104216465...`, residual `-1.0907e-73 Pa`, and a force difference of only `2.09e-14 N` from the frozen float oracle. The reported native gates use the frozen oracle; the high-precision result is a numerical cross-check, not a post-hoc tolerance change.

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

Both FEBio attempts used unique directories, absolute input/output paths, `-noappend -noconfig`, a `120 s` per-attempt bound, and environment thread limits `OMP_NUM_THREADS=2`, `MKL_NUM_THREADS=2`, `OPENBLAS_NUM_THREADS=2`, and `NUMEXPR_NUM_THREADS=2`. The wrapper observed only the owned direct child; it does not claim descendant enumeration or actual thread count.

| Attempt | Process exit | Normal termination | Direct final rows | Result |
|---|---:|---|---:|---|
| `attempt-01-febio` | `0` | yes | `219` nodes / `95` elements | **failed observation**: FEBio skipped the first requested ID on each wrapped output-selection line; retained and not accepted |
| `attempt-02-febio` | `0` | yes | `231` nodes / `100` elements | accepted bounded observation |

Attempt 01 is intentionally not presented as success. Its solver completed normally, but its direct logfile had incomplete ID sets and the analysis exited `1`. The input-generation defect was corrected in a fresh template by making each FEBio logfile-selection list one physical line; no material, mesh, BC, analytic reference, executable, or frozen numeric tolerance changed.

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

The launch inputs and all pre-launch evidence were frozen with the Windows `ReadOnly` attribute and hashed. The final verification covered three manifests: `25` files before attempt 01, `44` files before attempt 02, and `60` files in the final sweep. `freeze-verification-final.json` reports zero missing files, zero size mismatches, zero hash mismatches, and every recorded file still `ReadOnly`.

The before/after prior-study manifest compared `3,190` files across the ten existing ignored verification studies, including content hashes and Windows attributes. The comparison passed exactly; prior ignored studies were not modified. The source mesh copy retained the accepted SHA-256 shown above.

## 8. Exact commands and gate results

All commands were run from the repository root with Python 3.12 and absolute paths inside the ignored study root. The preparation and negative validation commands returned:

```text
capture_prior_manifest.py                         exit 0; 3190 prior files captured
prepare_free_dof.py                              exit 0; 231 nodes, 100 Tet10, 545 free DOFs
negative_probes.py                                exit 0; both expected child validators exit 1
prepare_attempt.py (attempt-01)                  exit 0; XML preflight, 545 free DOFs, outputs absent
freeze_launch.py (before attempt-01)             exit 0; 25 files ReadOnly
run_native_attempt.py (attempt-01)               wrapper exit 0; FEBio exit 0; normal termination
analyze_native_attempt_v2.py (attempt-01)         exit 1; incomplete 219/231 and 95/100 output rejected
prepare_free_dof_v2.py                            exit 0; formatting-only fresh template
prepare_attempt.py (attempt-02)                  exit 0; XML preflight, 545 free DOFs, outputs absent
freeze_launch.py (before attempt-02)             exit 0; 44 files ReadOnly
run_native_attempt.py (attempt-02)               wrapper exit 0; FEBio exit 0; normal termination
analyze_native_attempt_v2.py (attempt-02)         exit 0; all frozen numeric gates true
verify_freeze.py                                  exit 0; all recorded hashes/attributes matched
final_audit.py                                    exit 0; all six preservation/attempt checks true
freeze_launch.py (final)                         exit 0; 60 files ReadOnly
verify_freeze.py (final)                         exit 0; three manifests, zero mismatches
```

The repository product gates were not run for this documentation-only native observation: `python -m pytest`, Ruff format/check, mypy, build, installed smoke, official FBS, Studio, real-model E2E, and BottomFrame E2E remain unverified. They must not be inferred from this synthetic result.

## 9. Unverified boundaries and next task

- No product Gmsh/FEBio adapter, schema, CLI, solver ownership implementation, or reader was changed or accepted.
- The native result supports only this SI cube, this Tet10 input, this material parameterization, these four normal-component BCs, and this small compression. It does not establish general free-DOF, support, contact, rigid-body, or nonlinear solver robustness.
- The XPLT was generated and hashed only; no official FBS, XPLT-reader, compression, state, or variable-semantic claim follows.
- FEBio Studio was not launched. No GUI/post-processing or final-state display evidence exists.
- No real `02_CAE` data, credentials, real CAD/model, BottomFrame, official external FBS, or product release gate was accessed.
- No push or V2 integration was performed. The next task is an independent read-only review of this exact clean docs-only commit and the ignored evidence hashes.
