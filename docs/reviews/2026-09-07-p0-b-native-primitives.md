# P0-B Gmsh native primitive probe記録

日付: 2026-09-07
対象: P0-B 環境同定およびGmsh OpenCASCADE 3 primitiveの独立合成probe
調査開始base: `9392c29ebf6bd8eeacda307059a39ff4392c11d7`
ブランチ: `codex/p0-b-compatibility-research`
remote: `https://github.com/A6721jpn/cae-harness.git`（`REMOTE_CONFIGURED`）

## 1. 判定

freshなPython 3.12環境へ公式PyPIのGmsh 4.15.2を固定導入し、Gmsh native Python/OpenCASCADE APIで球・円柱・直方体を独立生成した。生成したSTEPのfresh process再読込、STEP単位文字列の確認、`Geometry.OCCTargetUnit="M"`の座標スケール、Gmshの二次四面体mesh情報までを観測した。

この記録の判定は次のとおりである。

> `GMSH NATIVE SYNTHETIC OBSERVATION RECORDED; FEBio/PRODUCT COMPATIBILITY UNVERIFIED`

Gmshの3形状・STEP roundtrip・Gmsh-native Tet10観測は合成probeの証拠である。FEBioの`.feb`入力、FEBio Tet10節点順との相互運用、FEBio解析、FBS、XPLT reader、FEBio Studio、実モデル、BottomFrameは今回実施していない。

この記録は、次の2権威文書を置き換えない。

- [設計仕様書](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md)
- [実装・検証計画](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md)

独自probe IDは権威Gateではない。今回の部分証拠は、主に権威計画のGM-01（STEP単位・ボディ選択・3種類の剛体生成の寸法・姿勢）とGM-02（領域・節点順・面方向・Tet10・接触面の非共有・品質）へ対応する。権威Gateの合格、製品経路の完成、FEBio互換性の宣言はしない。

## 2. 証拠の所在と実行境界

すべての観測script、venv、wheel、合成STEP、mesh結果、stdout/stderr、JSON、hashはGit追跡外の次のディレクトリへ保存した。

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-native-primitives-01
```

完全なrun索引は次である。各runの`command.json`に実際のargv、cwd、開始・終了時刻、60秒timeout、process exit、実行ファイルhash、script hash、stdout/stderrのpath・長さ・SHA-256を保存している。

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-native-primitives-01\evidence-index.json
```

最終v3 runの観測検証JSONは次である。これは製品testや互換性acceptance gateではない。

```text
C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-native-primitives-01\evidence-validation-v3.json
SHA-256: 57CCF98ACD506BC638A0B8673DBF50D5E23A82A72580245D7F98FA1427B14189
```

観測runnerとprobeのsource hashは次のとおりである。

| 用途 | 絶対パス | SHA-256 |
|---|---|---|
| run metadata / timeout / stdout・stderr収集 | `C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-native-primitives-01\scripts\run_command.py` | `D06B48C4F2B1902241D096E08818521BA992F86F95DE05E980D313FE54EABED1` |
| Gmsh module / primitive / STEP / mesh probe | `C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-native-primitives-01\scripts\gmsh_native_probe.py` | `741C78CF72826C78C69962507AAB60C2B82FF8D925FC08EBF18828AE746B792F` |

各runは独立した子processとしてrunnerから起動し、cwdはこのrepository root、timeoutは60秒以下とした。今回のrunは実CAEデータ、認証情報、デスクトップGUI状態、旧モデル、旧reader、旧repositoryを使用していない。

## 3. 環境同定

### 3.1 PythonとGmsh

| 項目 | 観測値 | exit / hash | 判定境界 |
|---|---|---|---|
| Python executable | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe` / Python 3.12.10 | venv作成 exit 0 | このprobe用環境のみ |
| venv | `...\.local\verification\P0B-native-primitives-01\venv` | `venv-create.json` | 既存venv・機械全体の環境は変更していない |
| PyPI source | [gmsh 4.15.2](https://pypi.org/project/gmsh/4.15.2/) | download exit 0 | 公式PyPI wheelの取得記録のみ |
| wheel | `gmsh-4.15.2-py2.py3-none-win_amd64.whl`、42,235,999 bytes | `7B36083BB410FA27C5D0E052929D1A9844A5B09169D66017B72B41AABD49D711` | wheelの同一性を固定 |
| pip install | wheelを`--no-index --no-deps`でinstall | exit 0 | 依存解決・他環境への変更なし |
| Python module | `...\venv\Lib\site-packages\gmsh.py`、version 4.15.2 | `A56EBE69DC57A3EA15EEE191CAE4F1881B06784174B2D9DF306A98BDD8B06606` | Gmsh Python APIの観測実体 |
| loaded native library | `...\venv\Lib\gmsh-4.15.dll`、89,212,416 bytes | `6CAC3EEFB477265D9FA60BBD869DBBBF7C7CA4CB308C0F8F2B43E91D43DE3C1C` | Python moduleが実際にロードしたnative DLL |

`module-info-v2`のGmsh module probeはexit 0であり、Python 3.12.10、Gmsh 4.15.2、module path、native DLL path・hashをJSONへ保存した。これはインストール・APIロードの観測であり、製品のGmsh adapter完成を示さない。

### 3.2 FEBio / FEBio Studioの同定のみ

PMから明示された本体pathだけを読取り、solverの版表示を一度観測した。Studio GUIは起動していない。

| 実体 | path | SHA-256 | 観測 |
|---|---|---|---|
| FEBio solver | `C:\Program Files\FEBioStudio\bin\febio4.exe` | `03B9DB12C4B3E2ED0CF027BE6B8B5D0CEF2D4EDC9EAB26F5A2BD8193EFB770C9` | `febio4 -info -norun -noconfig` stdout: `compiled on Feb 25 2026`, `FEBio version = 4.12.0`; exit **1**、stderr empty |
| FEBio Studio | `C:\Program Files\FEBioStudio\bin\FEBioStudio.exe` | `703AE324AE46AB03E9E5389116FA90AF5EFFE39364ED136DD6041AB8264252AE` | GUI未起動・XPLT未読込 |

FEBioのexit 1はそのまま記録する。stdoutの版表示が得られたという観測であり、solver起動正常、解析成功、XPLT生成、FBS、Studio読込の証拠ではない。solverのraw stdout SHA-256は`374271884EBF61519CC964AB46CE820017DB4898E8CB4AD70340D0B0DC11B7A4`である。

## 4. 事前定義した合成primitive

出所は今回のprobe scriptが新規に作成した独立形状であり、実部品ではない。nominal座標はmmとして期待値を置いたが、API座標を物理単位と無条件にはみなさない。Gmsh APIの根拠は[Gmsh 4.15.2 OpenCASCADE API](https://gmsh.info/doc/texinfo/gmsh.html#Namespace-gmsh_002fmodel_002focc)である。

| 形状 | API入力 | 期待bbox（nominal mm） | 期待体積（nominal mm³） | closed-solidのprobe期待 |
|---|---|---|---|---|
| 球 | `addSphere(0, 0, 0, 10)` | `[-10, -10, -10, 10, 10, 10]` | `4000*pi/3 ≈ 4188.790` | 3D volume 1個、境界surface 1個 |
| 円柱 | `addCylinder(0, 0, 0, 0, 0, 20, 5)` | `[-5, -5, 0, 5, 5, 20]`、+z軸 | `500*pi ≈ 1570.796` | 3D volume 1個、境界surface 3個 |
| 直方体 | `addBox(0, 0, 0, 10, 20, 30)` | `[0, 0, 0, 10, 20, 30]`、x/y/z軸 | `6000` | 3D volume 1個、境界surface 6個 |

各形状は新規model・独立tagで生成し、形状同士をfuseせず、治具・部品の結合や節点共有を行っていない。bbox判定は`rel=1e-9`、`abs=1e-6`の幾何probe専用Assumptionである。これはsolverの物理許容差ではない。

## 5. Gmsh native生成結果

最終採用runは`runs\generate-v3-*`である。Gmsh `getBoundingBox`は初回runで端点の外側約`1e-7`のenclosure paddingを返した。初回は`abs=1e-9`を使っていたため、3件ともprocess exit 0・体積一致だがbbox比較のみfalseとなった。初回ログは削除せず`runs\generate-*`へ保持し、観測後に許容を`1e-6`へ明示変更したv2/v3を再実行した。v3では3件すべてbbox・体積・positive-volume/boundary proxyがtrueである。

| 形状 | 実測bbox（Gmshのpadding込み） | 実測体積 | boundary surface数 | v3 bbox/volume |
|---|---|---:|---:|---|
| 球 | `[-10.0000001,-10.0000001,-10.0000001,10.0000001,10.0000001,10.0000001]` | `4188.790204786391` | 1 | true / true |
| 円柱 | `[-5.0000001,-5.0000001,-1e-7,5.0000001,5.0000001,20.0000001]` | `1570.7963267948965` | 3 | true / true |
| 直方体 | `[-1e-7,-1e-7,-1e-7,10.0000001,20.0000001,30.0000001]` | `6000.0` | 6 | true / true |

v3 STEPのhashは次のとおりである。

| 形状 | STEP path | bytes | SHA-256 |
|---|---|---:|---|
| 球 | `...\.local\verification\P0B-native-primitives-01\steps-v3\sphere.step` | 2123 | `7F47B00A6918AA4B804E71BD7B201D2FDA8117C0EDB3428B5A70419DA80EB0C9` |
| 円柱 | `...\steps-v3\cylinder.step` | 5656 | `B2ACB9E35A83920463802942E66AE79E61B51B6DEE23BD55BD20F12722897348` |
| 直方体 | `...\steps-v3\box.step` | 15416 | `41D26B93FA806A52599D08EDA8D28CD6A920DFF8B26BD750E6947DEF44DE2C74` |

## 6. STEP単位・fresh roundtrip

3つのSTEP本文を直接検査し、いずれも次の長さ単位宣言を確認した。

```text
LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.)
```

各STEPを新しいGmsh processで`occ.importShapes`し、`target_unit=default`と`Geometry.OCCTargetUnit="M"`を別runにした。

| target | 形状 | process | bbox/volume | 観測体積 |
|---|---|---:|---|---:|
| default | 球・円柱・直方体 | 3、各exit 0 | 3/3 true / true | `4188.790204786392`, `1570.7963267948967`, `6000.0` |
| `M` | 球・円柱・直方体 | 3、各exit 0 | 3/3 true / true | `4.188790204786392e-6`, `1.5707963267948965e-6`, `5.999999999999999e-6` |

`target=M`では、nominal mmの座標期待値を`1e-3`倍、体積期待値を`1e-9`倍として照合し、3/3が一致した。この結果はGmsh 4.15.2の今回のSTEP・target設定に対するnative観測である。製品の単位契約、任意STEPの単位推定、FEBio入力単位、実部品の物理意味を確定しない。

## 7. Gmsh-native二次tet mesh

各fresh roundtrip processで`Mesh.MeshSizeMin=6.0`、`Mesh.MeshSizeMax=10.0`、`Mesh.MeshSizeFromCurvature=0`を設定し、3D mesh後に`mesh.setOrder(2)`を実施した。`getElementType("tetrahedron", 2)`は全形状でtype 11を返した。

| 形状 | element type/name/order | nodes/primary/local coordinate数 | Tet10 element数 | mesh node数 | face/edge node数（1 element分） |
|---|---|---|---:|---:|---|
| 球 | `11 / Tetrahedron 10 / 2` | 10 / 4 / 30 floats（10 triplets） | 151 | 333 | face 24 / edge 18 |
| 円柱 | `11 / Tetrahedron 10 / 2` | 10 / 4 / 30 floats（10 triplets） | 102 | 247 | face 24 / edge 18 |
| 直方体 | `11 / Tetrahedron 10 / 2` | 10 / 4 / 30 floats（10 triplets） | 243 | 544 | face 24 / edge 18 |

Gmshが返した局所座標は、一次4節点の`(0,0,0),(1,0,0),(0,1,0),(0,0,1)`に続いて、辺上の二次節点を含む30個のfloatである。faceは4面×6節点、edgeは6辺×3節点として保存した。各runの最初のelement node/face/edge tags、全local coordinates、element propertiesは、対応する`runs\mesh-v3-*\result.json`に保存している。

この観測はGmshの要素型・局所順・面/辺情報だけを示す。FEBioのTet10節点順、FEBio面順、FEBioでの受理、Jacobian品質、接触surface変換、Gmsh-FEBio mappingの合格は未検証である。

## 8. 実行件数とexitの要約

| run群 | 件数 | 実測結果 | process exit |
|---|---:|---|---|
| module info v2 | 1 | Gmsh 4.15.2、Python 3.12.10、module/native DLL path・hash | 0 |
| generate v3 | 3 | 球・円柱・直方体、bbox/体積/closed-solid proxy 3/3 | 0, 0, 0 |
| STEP roundtrip default v3 | 3 | bbox/体積 3/3、MILLI/METRE宣言あり | 0, 0, 0 |
| STEP roundtrip `M` v3 | 3 | scale `1e-3`・volume `1e-9`のbbox/体積 3/3 | 0, 0, 0 |
| Tet10 mesh v3 | 3 | type 11、order 2、3/3 mesh、face/edge情報 | 0, 0, 0 |
| FEBio version probe | 1 | version 4.12.0 stdout、stderr empty | **1** |
| native observation validation | 1 | `evidence-validation-v3.json`、失敗0 | 0 |

初回strict bbox probeは3件ともexit 0だが判定`bbox=false, volume=true`であり、成功件数へ数えていない。これはGmsh bbox enclosure paddingの観測を受けた許容Assumption修正の履歴である。

## 9. 権威Gateへの部分対応と未検証

| 独自probe | 対応する権威Gate | 今回の部分証拠 | 残る未検証 |
|---|---|---|---|
| `P0B-ENV-01` | GM-01、FB-01、VW-01、PK-01 | Python/Gmsh実体・版・module/DLL hash、FEBio/Studio本体path・hash、solver版表示 | solverの正常起動・解析、Studio起動、全local gate |
| `P0B-GM-CAD-01` | GM-01 | 3 primitiveの寸法・姿勢・bbox・体積、STEP生成・単位宣言・fresh再読込、target Mのnativeスケール | 製品adapter、任意STEP、実ボディ選択、FEBio剛体生成 |
| `P0B-GM-TET10-01` | GM-02 | Gmsh type 11、Tet10 properties/local coordinates、face/edge node情報、mesh counts | FEBio mapping、face orientation契約、Jacobian/退化品質、接触面独立性の製品検証 |

権威計画のFB-01/FB-02/FB-03、QA-01、VW-01、E2E-01/02/03はこのnative観測で合格扱いにしない。特にFB-02の実行所有・子孫drain・停止・timeout・残存プロセスは今回のGmsh probeに含まれない。E2E-02は許可された一般real STEP、E2E-03は最終BottomFrameであり、今回の合成形状と混同しない。

## 10. 未実施と次タスク

今回実施していないものは次のとおりである。

- FEBio `.feb`生成、実FEBio解析、材料・剛体・支持・接触のnative変換。
- FEBio Tet10節点順・面順の受理確認、JacobianのFEBio境界検証。
- FBS、XPLT生成、XPLT reader、圧縮、dictionary/state/必須変数、数値照合。
- FEBio Studio GUI起動、対象XPLT読込、最終state・変位表示確認。
- 製品src/tests/pyproject/dependency lockの変更、製品RED/GREEN、pytest/ruff/mypy/build/smoke。
- 公式FBS、実モデル、`02_CAE`、LLM経路、E2E-01/02/03、最終BottomFrame。

次タスクは別のnative調査として、許可された合成入力を用いたFEBio実行所有・drain、`.feb`材料/剛体/BC/接触変換、XPLT reader、独立数値照合、Studio read confirmationをそれぞれ記録することである。今回のGmsh証拠だけでFEBioまたは製品経路の互換性を宣言しない。
