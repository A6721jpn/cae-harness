# P0-B native elastic patch probe記録

日付: 2026-09-07
対象: P0-B の Gmsh Tet10 → FEBio 4.12.0 native elastic patch 互換性観測
調査開始base: `6e731712b5a69a9ebcffaa2251fac87bad496f23`
ブランチ: `codex/p0-b-compatibility-research`
remote: `https://github.com/A6721jpn/cae-harness.git`（`REMOTE_CONFIGURED`）
追跡変更: このMarkdownのみ。probe script、入力、結果、solver、実行メタデータはGit追跡外に保存した。

## 1. Executive summary / 判定

このprobeは、独立に生成したSI単位のTet10 cubeをGmsh 4.15.2からFEBio 4.12.0へ渡し、既知の均一変位場を等方線形弾性材料へ与えた合成native観測である。最終採用run `attempt-06` は、FEBio process exit `0`、`N O R M A L T E R M I N A T I O N`、63/63 node、24/24 elementの直接text出力、log、XPLTを得た。

計画済みの基準に対する結果は次のとおりである。

| 判定項目 | 参照値 | 観測値 | 相対誤差 | 判定 |
|---|---:|---:|---:|---|
| 軸方向代表応力 `sxx` | `-1000 Pa` | `-997.67702899 Pa` | `0.2323%` | `PASS`（1%以内） |
| x方向反力の大きさ | `0.100000 N` | `0.09982757252 N` | `0.1724%` | `PASS`（1%以内） |
| 変位場 | 解析値 | 最大絶対誤差 `3.30e-19 m` | — | `PASS`（63/63 node） |
| 反力釣合い x成分 | `0 N` | `-5.81e-11 N` | — | 符号・釣合いを観測 |

> **Inference**: この限定された合成入力について、Gmsh Tet10の明示的な節点順変換、FEBio 4.0 input構造、等方線形弾性、prescribed deformation、直接text出力は、インストール済みFEBio 4.12.0のnative経路で整合した。

この結果は、製品のFEBio adapter、FBS、XPLT reader、FEBio Studio、接触、剛体、neo-Hookean、実モデル、または最終BottomFrameの合格を意味しない。実機solverはFEBio 4.12.0であり、参照manualは4.7/4.9のページを含むため、版差の残る機能について能力宣言はしない。

## 2. Context / 問題と意思決定

権威計画は、材料・単位・Tet10節点順・応力・反力符号を、底面全面固定を解析解の代用にしない均一変位場で確認するsynthetic patchを指定している。計画の参照式は `F = E A delta / L`、入力は `E = 1 MPa`、`nu = 0.3`、`L = 10 mm`、`A = 100 mm²`、`delta = 0.01 mm`、合格基準は反力・代表応力の相対誤差1%以内である。

この記録の意思決定は、次の狭い問いに限定した。

1. Gmshの二次tet節点列を、FEBio Theory ManualのTet10順へ明示的に変換できるか。
2. そのmeshをFEBio 4.12.0が受理し、既知の変位場・応力・反力を返すか。
3. 直接text出力とXPLTの存在を、solverのexit・normal terminationと別々に保存できるか。

権威文書は[設計仕様書](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md)と[実装・検証計画](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md)である。この報告は権威文書を置き換えない。

## 3. Scope, conditions, and assumptions / 範囲・条件・仮定

### 3.1 実行範囲

- 合成cubeのみ。実部品、実STEP、`02_CAE`、認証情報、デスクトップ状態は使用していない。
- Gmsh 4.15.2 Python APIで新規にcubeを生成し、二次化してTet10を作成した。
- FEBio solverは `C:\Program Files\FEBioStudio\bin\febio4.exe`、表示版は `4.12.0`、SHA-256は `03B9DB12C4B3E2ED0CF027BE6B8B5D0CEF2D4EDC9EAB26F5A2BD8193EFB770C9`。
- 全nodeへ均一な `prescribed deformation` を与えた。底面全面固定の代用、接触、重力、剛体、時間依存材料は使用していない。
- `attempt-01`から`attempt-06`までを独立した子directoryへ保存し、失敗した入力・stdout・stderrを上書きしていない。

### 3.2 Assumption

| 項目 | 仮定 | 置換・確認条件 |
|---|---|---|
| 単位 | 入力・解析ともSI、Gmsh cubeの一辺は`0.01 m` | 製品の単位契約と等価単位試験で確認する |
| 材料 | FEBioの`isotropic elastic`、`E=1e6 Pa`、`nu=0.3` | 実材料カード、適用ひずみ範囲、温度依存性が確定した時に置換する |
| 変位場 | `F=diag(0.999,1.0003,1.0003)`の均一場 | 実ケースではauthoritativeな支持・運動条件を登録する |
| tolerance | 計画指定の応力・反力相対誤差1% | profileごとの正式なacceptance gateへ移す |
| node mapping | Gmsh APIのlocal coordinatesとFEBio Theoryの座標列を一対一照合する | 製品adapterで節点・面方向・Jacobian・集合所属を再検証する |

物理的な材料値、荷重、支持、接触、ROIをgeometryから推定していない。ここでの値はscreening用の合成入力であり、実部品の許容値ではない。

## 4. Method and evidence ledger / 方法と証拠台帳

実行証拠のrootは次である。

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-elastic-patch-01
```

最終recordは次である。

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-elastic-patch-01\attempt-06\attempt-record.json
SHA-256: 39AE1F428FCA894F0E4F8826E3E1A84357CA98289C14F5CC43307A0B1559B46E
```

probe sourceは追跡外である。

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-elastic-patch-01\elastic_patch_probe.py
SHA-256 (attempt-06): C01971A6B60DF0A5959CD18DAFB626EF8DAAC819049DAAB9F68FD48A2C5C5A3F
```

| ID | Type | Claim / value | Applicability | Source / raw evidence | Report section |
|---|---|---|---|---|---|
| PUB-01 | Published | FEBio CLIでinput、log、plotを指定する | FEBio User Manual 4.9の記法。local 4.12.0での実行結果は別証拠 | [CLI](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-2.3.html) | 5, 7 |
| PUB-02 | Published | v4 inputは`Mesh`、`MeshDomains`等を持ち、Tet10はsolid elementとして定義される | FEBio User Manualのv4形式 | [Mesh input](https://help.febio.org/docs/FEBioUser-4-9/UM49-3.6.2.1.html) | 5, 7 |
| PUB-03 | Published | Tet10のdocumented local order | FEBio Theory Manual 4.7 | [Tet10 theory](https://help.febio.org/docs/FEBioTheory-4-7/TM47-Subsection-4.1.4.html) | 5, 7 |
| PUB-04 | Published | `prescribed deformation` と `F` のinput記法 | FEBio User Manual 4.9 | [Prescribed deformation](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-3.11.10.html) | 5, 6 |
| PUB-05 | Published | `node_data` / `element_data` のoutput要求 | FEBio User Manual 4.9 | [Output logging](https://help.febio.org/docs/FEBioUser-4-9/UM49-3.19.1.1.html) | 5, 7 |
| PUB-06 | Published | `isotropic elastic` の材料名と`E`/`v`パラメータ | FEBio Feature Manual | [Isotropic elastic](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_isotropic_elastic/) | 5, 6 |
| PUB-07 | Published | Gmsh Tet10 type 11とAPI local-coordinate取得 | Gmsh 4.15.2 Reference Manual | [Gmsh manual](https://gmsh.info/doc/texinfo/gmsh.html) | 5, 7 |
| ASM-01 | Assumption | `E`, `nu`, `L`, `A`, `delta`とSI単位 | 計画が指定する合成screening入力 | 計画 §5.1と本probeのphysics record | 3, 6 |
| CALC-01 | Calculation | `sigma_xx=-1000 Pa`, `F_ref=0.100 N` | 小ひずみ均一場、接触なし | §6の式と代入 | 6, 7 |
| INF-01 | Inference | 最終native runはこのsynthetic profileの入力・出力境界を満たす | 実モデル・製品全体へ一般化しない | §7のruntime recordと計画の1% gate | 1, 7, 9 |

raw runtime recordは「自己申告したJSON」と混同しないよう、solverの直接process結果、cwd、argv、開始・終了時刻、timeout、stdout/stderr、各artifact SHA-256を同じrecordへ保存した。process exit `0`とJSON内の判定を別の事実として扱う。

## 5. Published compatibility facts / 公開仕様との対応

公開manualが直接支えるのはinput/outputの記法と要素・材料・境界条件の名称であり、local executableが実際に受理することは別のnative観測である。

- GmshのTet10 local coordinatesはGmsh APIから取得した。FEBio documented local coordinatesは、4 corner nodesに続く6 edge nodesの列として保存した。
- 照合結果はidentityではなく、Gmshの最後2点 `(0,0.5,0.5)` と `(0.5,0,0.5)` がFEBio列と逆順だった。したがって全elementへ、zero-based `[0,1,2,3,4,5,6,7,9,8]`、one-based `[1,2,3,4,5,6,7,8,10,9]` を明示適用した。
- FEBio v4 inputのmesh sectionは`<Mesh>`である。初期probeの`<Geometry>`は4.12.0に拒否されたため、`attempt-04`以降は`<Mesh>`を使った。
- `SolidDomain`の`mat`はこのinputでmaterial name `elastic_patch`を参照した。数値`1`を渡した`attempt-04`は拒否された。
- outputのnode listは1行に連結した。16個ごとの改行を含む初期形式では3 node（17, 33, 49）が出力から欠落したため、`attempt-06`では63/63を直接確認した。

この節の公開情報は、Gmsh-FEBio adapterが任意の要素、面、surface、単位、材料、接触を処理できることを証明しない。

## 6. Calculation method / 計算方法

### 6.1 入力と次元

```text
L       = 0.01 m
A       = L^2 = 1.0e-4 m^2
E       = 1.0e6 Pa
nu      = 0.3
delta   = 1.0e-5 m
eps_x   = -delta/L = -1.0e-3
eps_y,z = -nu*eps_x = 3.0e-4
F       = diag(0.999, 1.0003, 1.0003)
```

参照応力は、単軸応力状態を作るPoisson収縮を含む均一小ひずみ場に対して、

```text
sigma_xx = E * eps_x
         = (1.0e6 Pa) * (-1.0e-3)
         = -1.0e3 Pa
```

参照反力は、権威計画の式に従い、

```text
F_ref = E * A * delta / L
      = (1.0e6 N/m^2) * (1.0e-4 m^2) * (1.0e-5 m) / (1.0e-2 m)
      = 1.0e-1 N
```

次元は `Pa*m^2*m/m = N` で整合する。`x=0` 面と`x=L`面の反力は符号が反対になり、合計は0へ近づくことを確認した。

### 6.2 meshとinput

| 項目 | 最終観測 |
|---|---:|
| Gmsh element | type `11`, `Tetrahedron 10`, order `2` |
| node / element | `63 / 24` |
| x=0 / x=L nodes | `13 / 13` |
| corner Jacobian determinantの最小値 | `2.5e-7 m^3`（幾何probeの値） |
| Gmsh→FEBio permutation | `[1,2,3,4,5,6,7,8,10,9]`（one-based） |
| FEBio input | `febio_spec version="4.0"`, `Module=solid`, `Mesh`, `MeshDomains`, `isotropic elastic`, `prescribed deformation` |

corner Jacobianは独立に計算したcorner orientation checkである。FEBioの全積分点品質、面orientation、接触surface独立性を代替しない。

## 7. Native run results / native観測結果

### 7.1 最終run

最終採用artifactは次のとおりである。

| artifact | path | bytes | SHA-256 |
|---|---|---:|---|
| input | `...\attempt-06\elastic-patch.feb` | 8458 | `5850B0EFD92F1FF5056742A631993D1D60B58A59D6546F4B9D0A5EC39F0DE9A8` |
| mesh record | `...\attempt-06\mesh.json` | 12838 | `05E51722C02E64A74D6A8169FC2BAB246FAE0BCD57E3DB489A7F1FAEF11228DD` |
| solver stdout | `...\attempt-06\solver-stdout.txt` | — | `5DD7711D537E372175F2BE217B8E7639A385EB6300566C0F2DFF78747707DE6C` |
| solver stderr | `...\attempt-06\solver-stderr.txt` | 0 | `E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855` |
| FEBio log | `...\attempt-06\elastic-patch.log` | 9782 | `60C8C16B8BD3A302B401E0F3A584CF87BA172BE5E43BEF06A1AB237BC828DD8B` |
| XPLT | `...\attempt-06\elastic-patch.xplt` | 8966 | `0B835386CF2606BFD408379DB9A99D2696307C62D321A13D11D9D3488F4094A4` |
| node data | `...\attempt-06\node-data.txt` | 8594 | `872BE160D58897D40D6232D99F5F030AED88382192CC9C02874B4A27A8D845CD` |
| element data | `...\attempt-06\element-data.txt` | 2950 | `EE8A253517F7C02BA1B20250DB390BDAD7150539B23BB7877931300B01348336` |

pathの`...`は上記probe rootの省略表記であり、raw recordには絶対pathを保存している。solver commandは次のとおりである。

```text
C:\Program Files\FEBioStudio\bin\febio4.exe
  -i <attempt-06>\elastic-patch.feb
  -o <attempt-06>\elastic-patch.log
  -p <attempt-06>\elastic-patch.xplt
  -noappend -noconfig
```

processはtimeoutなし、duration `177.5877 ms`、exit `0`、stderr empty、stdoutにnormal terminationを含む。直接text parserはnode `63`、element `24`を得た。

### 7.2 観測値と判定

| output | 期待 | 観測 | 判定 |
|---|---:|---:|---|
| max absolute displacement error | 変位場一致 | `3.3034284943e-19 m` | 値・向き一致 |
| element mean `sxx` | `-1000 Pa` | `-997.677028991708 Pa` | relative `0.2323%`, `PASS` |
| element mean `syy` | `0 Pa` | `0.375375375276 Pa` | `sxx`の`0.0376%`、副次残差 |
| element mean `szz` | `0 Pa` | `0.375375375261 Pa` | `sxx`の`0.0376%`、副次残差 |
| element mean shear components | `0 Pa` | mean `(sxy, syz, sxz) = (4.753211441839583e-12, 2.5622747443535835e-11, -2.8051239481602082e-11) Pa`; 最大絶対値 `2.8051239481602082e-11 Pa` | 数値残差 |
| x=0 reaction x | `+0.100000 N` | `+0.0998275724668 N` | sign/equilibrium一致 |
| x=L reaction x | `-0.100000 N` | `-0.0998275725249 N` | magnitude relative `0.1724%`, `PASS` |
| x reaction sum | `0 N` | `-5.8079305254e-11 N` | 釣合い観測 |

### 7.3 失敗履歴と修正境界

失敗を成功件数へ加えていない。各attemptのrecord/errorはprobe rootに保持した。

| attempt | process | 事象 | artifact / record SHA-256 |
|---|---:|---|---|
| 01 | 未実行 | Gmsh local coordinatesとFEBio documented orderのidentity前提を検出し、solver前に停止 | `probe-error.json` `B8DC5F1F4BDAAF19AC63D557CCC939B708A3EE31CE328CC604C2DE14A506CCA9` |
| 02 | `1` | `max_ups`がunrecognized tag。log/XPLT/text outputなし | record `AC032ABD1FD5D86E41BDA863D7083B38ACEE904ABA9DD54D9F5F91F6E0EC7E35` |
| 03 | `1` | v4 inputの`Geometry`がunrecognized tag。log/XPLT/text outputなし | record `610A4E7F7A567C552EE9B136136C4D3FCE75FE8B01A01E33113882D21F34EB03` |
| 04 | `1` | `SolidDomain mat="1"`がinvalid。log/XPLT/text outputなし | record `ACFB09AFA101243F8309443A2BB75848DDA03BCA16B5C821C80F58912A7285D1` |
| 05 | `0` | solverは完了したが、改行付きnode item listで17/33/49が欠落し、normal-term regexもfalse。最終合格には不採用 | record `5E9894AE1C2E77E726B876100B743C75F1FBB63F119CD9D17F42623777104636` |
| 06 | `0` | node listを一行化、normal terminationの空白を認識し、63/63 nodeとnormal terminationを確認 | record `39AE1F428FCA894F0E4F8826E3E1A84357CA98289C14F5CC43307A0B1559B46E` |

この修正はすべてGit追跡外のprobe scriptに限定され、製品src/tests/authority docsは変更していない。

## 8. Recommendations / 推奨

1. **このsynthetic profileのnative observationをcompatibility matrixの部分証拠として登録する。** 根拠はattempt-06のexit、normal termination、直接text、XPLT、数値照合である。ただし能力公開はこのprofileの範囲に限定する。
2. **Gmsh→FEBio変換はidentity mappingを禁止し、local-coordinateから得た明示 permutationと、面順・orientation・Jacobian・set所属の検証結果をartifactへ保存する。** attempt-01の停止がこのリスクを示した。
3. **出力判定はprocess exit、normal termination、直接text、XPLT、reader結果を別gateにする。** attempt-05はsolver exit `0`でもparser欠陥を残したため、単一のsuccess flagを使わない。
4. **`attempt-06`のXPLTを使うreader probeを次に行う。** XPLTが存在することだけではdictionary、compression、state、必須variables、数値読出しの証拠にならない。

## 9. Verification plan / 残る検証計画

| 次の検証 | 入力 | 計測 | 合格基準 | 現状 |
|---|---|---|---|---|
| independent XPLT reader | `attempt-06\elastic-patch.xplt` | mesh/state/dictionary/variableとstress・displacementの独立読出し | 直接text・解析値と別実装で一致。compression有無を明示 | 未実施 |
| unit-equivalent patch | SIと等価な別単位入力 | stress/force/displacement | 同じSI結果、単位変換を記録 | 未実施 |
| rigid/kinematic profile | 新規synthetic rigid input | rigid position/force/torque | 解析運動・反力・停止/dainを事前基準で照合 | 未実施 |
| frictionless/friction contact | 新規synthetic contact pair | contact pressure/force/gap/status、力釣合い | profileごとの事前基準。Hertzは5%以内、mesh refinementも別確認 | 未実施 |
| Studio read confirmation | 採用XPLT | GUI起動、対象file読込、最終state、変位表示 | 手順とcaptureを保存。起動だけではread confirmationとしない | 未実施 |
| product gate | 製品のnative CLI経路 | pytest、ruff、mypy、build、installed smoke | リポジトリのrequired local gatesをfreshに実行 | 未実施 |

実FEBio/FBS/Studio/model/BottomFrameの証拠は、このsynthetic native observationとは別に収集する。

## 10. Limitations and unresolved items / 限界と未解決

- これはGmshとインストール済みFEBioの合成観測であり、CAE Harness product codeのtestではない。
- FEBio local executableは4.12.0、公開参照ページは4.7/4.9を含む。版差、solver build差、将来更新への互換性は未確定である。
- Gmsh Tet10のexplicit permutationはこのGmsh API結果とFEBio Theoryのdocumented coordinatesに基づく。任意の要素型、surface face order、outward orientation、曲面、複数domainを保証しない。
- corner Jacobianの最小値は幾何orientationのsanity checkであり、全積分点、退化、mesh convergence、接触面の独立性を保証しない。
- `isotropic elastic`の合成`E`と`nu`はscreening入力であり、実材料のgrade、温度、rate、製造条件、許容値ではない。
- 全63 nodeへaffineな`prescribed deformation`を与えているため、変位一致はprescribed fieldとoutputの整合性を確認するものであり、free-DOF displacement solveの独立検証ではない。
- 記録したSHA-256はmain `febio4.exe`のdigestである。実行時にloadedされたsolver DLL、plugins、configurationのidentityは完全には固定・検証していない。
- XPLTは生成されたが、compression、header/dictionary/state、readerの独立数値照合は未実施である。
- FEBio Studioは起動していない。GUI read confirmation、FBS、実モデル、実行所有、子孫drain、timeout後cleanup、final BottomFrame E2Eは未検証である。
- pytest、ruff、mypy、build、installed smoke、公式FBS、real E2E、product release gateはこのtaskでは実行していない。未実行をpassとして数えていない。

したがって、この報告の最終判定は次である。

> `P0-B SYNTHETIC NATIVE ELASTIC PATCH OBSERVATION RECORDED; PRODUCT/FBS/XPLT-READER/STUDIO/REAL-MODEL COMPATIBILITY UNVERIFIED`

## 11. References / 参考資料

1. [FEBio LLM CAE Harness design v2](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md) — repository authority.
2. [FEBio CAE Harness greenfield plan](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md) — repository authority; §5.1 synthetic elastic patch.
3. [Gmsh 4.15 Reference Manual](https://gmsh.info/doc/texinfo/gmsh.html) — OpenCASCADE and element/API reference.
4. [FEBio User Manual 4.9, command line](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-2.3.html) — `febio4` CLI options.
5. [FEBio User Manual 4.9, mesh input](https://help.febio.org/docs/FEBioUser-4-9/UM49-3.6.2.1.html) — v4 mesh/Tet10 input structure.
6. [FEBio Theory Manual 4.7, quadratic tetrahedron](https://help.febio.org/docs/FEBioTheory-4-7/TM47-Subsection-4.1.4.html) — Tet10 local order reference.
7. [FEBio User Manual 4.9, prescribed deformation](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-3.11.10.html) — `F` boundary input.
8. [FEBio User Manual 4.9, node/element output](https://help.febio.org/docs/FEBioUser-4-9/UM49-3.19.1.1.html) — logfile output requests.
9. [FEBio Feature Manual, isotropic elastic](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_isotropic_elastic/) — material type and parameters.
