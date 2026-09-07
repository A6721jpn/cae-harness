# P0-B compressible neo-Hookean native observation

Date: 2026-09-07

Target: bounded synthetic homogeneous-deformation observation of FEBio's unconstrained `neo-Hookean` material.

Base: `f903575283136ffe45ff0c703bc3f6fab3462285`

Branch: `codex/p0-b-neo-hookean-observation`

Remote: `https://github.com/A6721jpn/cae-harness.git` (`REMOTE_CONFIGURED`)

Tracked change: this Markdown file only. The generator, inputs, solver results, hashes, and runtime metadata are ignored under `.local/verification/P0B-neo-hookean-observation-01`.

## 1. Executive summary / 判定

This is a synthetic native observation, not product verification. A new local generator created a 10 mm cube with 125 nodes and 48 positive-orientation Tet10 elements. Every node received the prescribed affine field `u=(F-I)X` for two diagonal deformation gradients:

| Case | `F` | Native result |
|---|---|---|
| small compression | `diag(0.999, 1, 1)` | exit `0`, normal termination, cleanup verified |
| finite compression | `diag(0.95, 1, 1)` | exit `0`, normal termination, cleanup verified |

The direct FEBio text outputs matched the analytic Cauchy stress, face reaction forces, prescribed displacement, and `sed` strain-energy-density reference to the frozen preflight thresholds. The maximum nonzero stress relative residual was `1.75e-12` for the small case and `1.12e-12` for the finite case; the maximum face-reaction relative residual was about `1.0e-9`. The finite-case largest absolute reaction residual was `7.05e-9 N`, below the frozen `1e-8 N` equilibrium bound.

This supports only the following narrow inference:

> The installed FEBio 4.12.0 native executable accepted this newly generated homogeneous Tet10 input and returned numerically consistent neo-Hookean stress, reaction, displacement, and direct `sed` output for the two prescribed fields.

It does not verify the product adapter, free-DOF equilibrium, contact, FBS, XPLT reader, FEBio Studio, real models, production material calibration, or any P0/P3/E2E gate.

## 2. Scope and authority boundary / 範囲

The authority documents remain the [design specification](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md) and [greenfield plan](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md). This report is an investigation record and does not amend either document.

- The model is synthetic and was generated inside this V2 investigation. No real `02_CAE` data, STEP, credentials, or desktop state was used.
- The material is an explicit screening input: `E=1 MPa`, `nu=0.3`, type `neo-Hookean`.
- The cube uses mm, MPa (`N/mm^2`), N, and N*mm as the external unit convention. FEBio input values are dimensionally interpreted using that convention; the observation does not claim a product-wide unit contract.
- All nodes are prescribed. This deliberately measures material/output/mapping behavior, not an independently solved boundary-value problem.
- No contact, gravity, rigid body, time-dependent material, FBS, Studio UI, or product code was used.

## 3. Frozen analytic preflight / 事前固定値

The immutable preflight was written before the first native solver invocation:

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-neo-hookean-observation-01\preflight.json
SHA-256: E34C225C6883D3B29A73D9010DAB5CF15C82547286FFB752A81A890A9492F164
```

It was marked read-only after validation. The constitutive model is the manual's

The supplemental provenance record observed filesystem creation of `preflight.json` at `2026-09-07T06:40:24Z`, before the first generated input at `06:43:42Z` and the first native solver start at `06:44:06.789381Z`.

```text
W = mu/2 * (I1 - 3) - mu*ln(J) + lambda/2 * ln(J)^2
mu = E / (2*(1+nu))
lambda = nu*E / ((1+nu)*(1-2*nu))
```

For the diagonal field `F=diag(a,1,1)`, the preflight evaluates

```text
J = a
I1 = a^2 + 2
B = F*F^T
sigma = (1/J) * (mu*(B-I) + lambda*ln(J)*I)
P = mu*(F-F^(-T)) + lambda*ln(J)*F^(-T)
```

`W` is retained as reference-volume energy density. The diagnostic current-volume density is `W/J`; the reference total is `W*V0` and the current-volume diagnostic total is `W*J*V0`. Native total-energy semantics are not inferred from the `sed` field.

With `V0=1000 mm^3`, reference face area `A0=100 mm^2`, the x-face current area is `100 mm^2`, and the y/z current face area is `a*100 mm^2`. The low-coordinate face reaction has positive normal sign and the high-coordinate face reaction has negative normal sign.

| Case | `J` | `I1` | `W` reference density (MPa) | `sigma_xx` (MPa) | `sigma_yy=sigma_zz` (MPa) | x-face magnitude (N) | y/z-face magnitude (N) | `ux` at x=10 (mm) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| small | `0.999` | `2.998001` | `6.734939506222832e-7` | `-0.001347405290049771` | `-0.000577789520434012` | `0.134740529004977` | `0.057721173091358` | `-0.0100000000000000` |
| finite | `0.95` | `2.9025` | `0.001737133047844787` | `-0.070623458130091` | `-0.031149773919565` | `7.062345813009145` | `2.959228522358687` | `-0.500000000000000` |

Frozen acceptance bounds were:

| Quantity | Bound |
|---|---:|
| nonzero stress/face-force relative residual | `1%` |
| nonzero energy relative residual, only under the reference-density convention | `1%` |
| expected-zero stress/shear component | `1e-8 MPa` absolute |
| global reaction equilibrium | `1e-8 N` absolute |
| prescribed displacement | `1e-10 mm` absolute |
| homogeneous element stress spread | `1e-8 MPa` absolute |

No bound was tuned after seeing native output.

## 4. Published compatibility facts / 公開仕様

- The [FEBio 4.13 neo-Hookean feature page](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_neo-hookean/) names the material type, `E`/`v` inputs, the `W` formula, the `E`/`nu` to `mu`/`lambda` mapping, and the displacement-based formulation caution.
- The [FEBio plot-variable page](https://febiosoftware.github.io/febio-feature-manual/plotvars/) identifies `stress` as Cauchy stress, `reaction forces` as nodal reaction forces, `strain energy density` as `Psi(C)`, and `current element strain energy` as the element's total energy at the current configuration.
- The v4 logfile syntax and `element_data` `sed` variable are recorded against the [FEBio User Manual output section](https://help.febio.org/docs/FEBioUser-4-9/UM49-3.19.1.3.html). Local executable acceptance is a separate native observation below.

These references establish names and formulas; they do not establish product adapter support or the exact semantics of every XPLT/logfile quantity across executable builds.

## 5. Generator, mesh, and input / 生成物

The new ignored generator is `neo_hookean_probe.py`; it does not import product code or external-repository code. It constructs a structured `0,5,10 mm` grid, splits each 5 mm cube into six positively oriented corner tetrahedra, and adds six shared midside nodes in the FEBio Tet10 edge order `(1-2, 2-3, 3-1, 1-4, 2-4, 3-4)`.

Independent preparation checks gave:

| Check | Result |
|---|---:|
| nodes | `125` |
| Tet10 elements | `48` |
| minimum corner determinant | `125 mm^3` |
| total corner-tet volume | `1000.0000000000007 mm^3` |
| duplicate node coordinates | `0` |

Each input includes `neo-Hookean`, `E=1.0`, `v=0.3`, a one-step static analysis, all-node `prescribed deformation`, direct node fields (`x;y;z;ux;uy;uz;Rx;Ry;Rz`), direct element fields (`sx;sy;sz;sxy;syz;sxz;sed`), and XPLT requests for displacement, stress, reactions, strain-energy density, current element strain energy, and deformation gradient.

## 6. Native execution ledger / 実行台帳

Evidence root:

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-neo-hookean-observation-01
```

The exact native command recorded in each attempt is:

```text
C:\Program Files\FEBioStudio\bin\febio4.exe
  -i <unique-attempt>\neo-hookean.feb
  -o <unique-attempt>\neo-hookean.log
  -p <unique-attempt>\neo-hookean.xplt
  -noappend -noconfig
```

The absolute interpreter was `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe` (`3.12.10`). Each attempt used a unique directory, one native process, and environment thread limits of `2` for OpenMP/MKL/OpenBLAS/NumExpr. The bounded ledger allowed six attempts; two were used and both were successful.

| Attempt | UTC start | Duration | Child PID | exit | normal termination | cleanup |
|---|---|---:|---:|---:|---|---|
| `attempt-01-small-compression` | `2026-09-07T06:44:06.789381Z` | `0.156 s` | `42848` | `0` | yes | verified |
| `attempt-02-finite-compression` | `2026-09-07T06:44:38.589706Z` | `0.203 s` | `47948` | `0` | yes | verified |

Solver executable: `C:\Program Files\FEBioStudio\bin\febio4.exe`, SHA-256 `03B9DB12C4B3E2ED0CF027BE6B8B5D0CEF2D4EDC9EAB26F5A2BD8193EFB770C9`. The version `4.12.0` was observed in solver stdout; the PE version fields were blank. `-noconfig` was used and stdout stated `Starting without configuration file`.

The on-disk binary-directory inventory is in `native-environment.json` (SHA-256 `D65BC59E6C56AF6E92693D43C18AA1AA53AB739E2E88B439AF1549B1FFAD535F`). It is an identity record, not a loaded-module snapshot: no debugger or loader instrumentation was used.

Selected artifact hashes:

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| small input | `11644` | `A1E7EBE1C007E3BE8461F30E635DF18A89584FE96FE0992E609B9F276C32B73C` |
| small log | `9970` | `CC32A8CF7C0135EAB5891A034C138611ADD85F9C4ABA8EEF5E916DB9C774DBF1` |
| small XPLT | `21444` | `36A82902E721830DB5D847F11D85D9791F083DAABA96EA417B7F1C864727B01C` |
| small node text | `13217` | `EDB5938222B51731FAB1E0A3BA2930E16301C7061A9D1D3E52DE41C1BB6DA8BF` |
| small element text | `7331` | `686D98982AFFDC1400F5FF05B0C80E1F00C904D019A5C1E5198029314B16F329` |
| finite input | `11658` | `E24C1B1467A23E39B7F69DF991805A223C735AC67D86526DE05B3A80CDCFAD7D` |
| finite log | `9974` | `49B166C9D87067E264D4B3034CC4911FD491C8DAAF7A73EDED251EDA769E1B7B` |
| finite XPLT | `21444` | `46B24D116ED8A69BE125A7E2C31128CFFB0BEC3527A0F07BE37696BD05586F84` |
| finite node text | `12849` | `068606538DFACA042622458609667A3E63804E8D1D7A666AAE4E6EE5C0000869` |
| finite element text | `7046` | `33E0D4CF9CB2D10461D2C6A68F157B1C1BAF69C5800B0B131C903FAA9E6C21CD` |

Both stderr files were zero bytes with SHA-256 `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855`. Full process metadata and all artifact hashes are in each `attempt-record.json`.

## 7. Native values and residuals / 数値照合

The independent text analyzer read only the final (`Step=1`) records and saved `analysis-index.json` (SHA-256 `9A0B9E99CFA66A9615E685BB52ED0C048263A7061EF52FE5F591F3175E1ABE1A`).

### 7.1 Stress and energy density

| Case | Component | Expected | Observed mean | Max absolute residual | Max relative residual |
|---|---|---:|---:|---:|---:|
| small | `sxx` MPa | `-1.347405290049771e-3` | `-1.347405290050000e-3` | `2.292e-16` | `1.701e-13` |
| small | `syy` MPa | `-5.777895204340115e-4` | `-5.777895204340000e-4` | `1.149e-17` | `1.989e-14` |
| small | `szz` MPa | `-5.777895204340115e-4` | `-5.777895204339792e-4` | `1.012e-15` | `1.751e-12` |
| finite | `sxx` MPa | `-7.062345813009145e-2` | `-7.062345813010000e-2` | `8.549e-15` | `1.210e-13` |
| finite | `syy` MPa | `-3.114977391956513e-2` | `-3.114977391960000e-2` | `3.487e-14` | `1.119e-12` |
| finite | `szz` MPa | `-3.114977391956513e-2` | `-3.114977391960000e-2` | `3.487e-14` | `1.119e-12` |

All element shear components were expected zero. The largest absolute shear residual was `1.308e-16 MPa` in the small case and `1.362e-16 MPa` in the finite case, below the frozen `1e-8 MPa` bound. Element stress spread was at most `1.001e-15 MPa` for the small case and below the printed precision for the finite case.

The direct logfile `sed` field was homogeneous and matched the frozen reference-volume `W`:

| Case | Expected `W` (MPa) | Observed mean (MPa) | Max relative residual | Result |
|---|---:|---:|---:|---|
| small | `6.734939506222832e-7` | `6.734939506535208e-7` | `7.976e-11` | `PASS` under the stated density convention |
| finite | `1.737133047844787e-3` | `1.737133047840000e-3` | `2.755e-12` | `PASS` under the stated density convention |

The total reference energies (`0.0006734939506222832 N*mm` and `1.7371330478447866 N*mm`) and current-volume diagnostics (`0.000672820456671661 N*mm` and `1.6502763954525472 N*mm`) were precomputed but not claimed as native total-energy results. The XPLT files were hashed only; no XPLT reader or viewer acceptance was claimed.

### 7.2 Prescribed displacement and reaction forces

Maximum displacement errors were `8.674e-18 mm` (small) and `4.441e-16 mm` (finite), both below `1e-10 mm`. Current-coordinate errors were zero at the reported precision.

For each case, the six face reaction sums had the preflight signs and magnitudes. The largest face-normal relative residual was `9.981e-10` (small) and `9.984e-10` (finite). The largest absolute face-normal residual was `1.345e-10 N` (small) and `7.047e-9 N` (finite), both below the frozen `1e-8 N` bound. Global reaction sums were at most `5.78e-14 N` (small) and `6.16e-13 N` (finite) by component.

This confirms reaction output against the analytic traction integral for this fully prescribed field. It is not a free-equilibrium or support/contact validation.

## 8. Failure ledger and verification status / 失敗台帳

There were no failed native attempts. Attempts `01` and `02` were the only solver processes and both produced their own input, stdout, stderr, log, XPLT, node text, element text, and process record. No artifact was reused by filename, overwritten, or recategorized.

Repository product gates were intentionally not run for this documentation-only native investigation. There is no manufactured RED/GREEN evidence. The following remain unverified:

- product `src`/CLI integration and all required pytest, ruff, mypy, build, installed-smoke gates;
- official FBS identity, receipt, named-pipe, freshness, and report boundaries;
- XPLT parsing, dictionary/state/variable validation, compression handling, and viewer acceptance;
- FEBio Studio GUI read confirmation;
- free-DOF equilibrium, contact, rigid-body, solver descendant drain, and timeout cleanup certification;
- real material values, real models, and final BottomFrame E2E.

## 9. Final bounded verdict / 最終判定

`P0-B SYNTHETIC NATIVE COMPRESSIBLE NEO-HOOKEAN OBSERVATION RECORDED; PRODUCT/FBS/XPLT-READER/STUDIO/FREE-DOF/REAL-MODEL COMPATIBILITY UNVERIFIED`

The candidate material is therefore recorded as a narrow synthetic native observation, not as production support.

## 10. References / 参考資料

1. [FEBio LLM CAE Harness design v2](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md) — repository authority.
2. [FEBio CAE Harness greenfield plan](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md) — repository authority.
3. [FEBio 4.13 neo-Hookean feature](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_neo-hookean/) — material name, parameters, constitutive energy, and Lamé mapping.
4. [FEBio 4.13 plot variables](https://febiosoftware.github.io/febio-feature-manual/plotvars/) — stress, reactions, strain energy density, and current element strain-energy labels.
5. [FEBio User Manual v4.9 logfile output](https://help.febio.org/docs/FEBioUser-4-9/UM49-3.19.1.3.html) — `element_data` and `sed` syntax reference.
