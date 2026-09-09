# P0-B 公式互換性調査記録

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

日付: 2026-09-07
対象: FEBio CAE Harness V2 の P0-B 公式互換性調査
調査時点対象コミット: `0cc803a6c5a568172662592e5ec932b9c7d09112`
独立レビュー修正ベース: `d88c6e9debb7d8f8dd9be58860e938b82b3952b0`
ブランチ: `codex/p0-b-compatibility-research`
remote: `https://github.com/A6721jpn/cae-harness.git`（`REMOTE_CONFIGURED`）

## 1. 結論

公式マニュアルから、候補経路の語彙・形式・検証すべき境界は整理できた。しかし、これはネイティブ互換性の合格証拠ではない。Gmsh、FEBio、FEBio Studioのローカル実体の絶対パス・版・ハッシュ、実行結果、XPLT読込、節点順変換、数値照合、Studioでの対象XPLT読込は本調査では未実施である。

したがって、本記録の判定は次のとおりとする。

> `P0-B DOCUMENTARY RESEARCH RECORDED; INDEPENDENT REVIEW REQUIRED; NATIVE COMPATIBILITY UNVERIFIED; P0 NOT READY`

公式資料を根拠に候補を実装可能と宣言してはならない。P0の対応表では、以下をすべて `UNVERIFIED` のまま保持する。

- 登録済みGmsh、FEBio、FEBio Studioの実体・版・ハッシュと、今回参照した公式資料との対応。
- GmshのTet10とFEBio入力の節点順・面節点順・面方向の変換。
- 候補の等方線形弾性、圧縮性neo-Hookean、剛体、処方変位、Sliding-Elasticの組合せ。
- 反力、接触診断、剛体履歴、応力・ひずみ等の要求変数の名前、配置、符号、状態。
- 圧縮を含む対象XPLTのreader互換性と独立した数値照合。
- FEBio Studioの起動と、対象XPLT・最終状態・要求変数の読込確認。

## 2. 範囲と判定規則

製品の権威文書は次の2文書だけとした。外部の公式マニュアルは、そこに記載された候補技術の事実を照合する補助資料であり、製品の対応宣言を直接決めるものではない。

- [設計仕様書](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md)
- [実装・検証計画](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md)

本記録では、証拠を次の語で分類する。

| 分類 | 意味 | 本記録での扱い |
|---|---|---|
| `Published` | 公式資料に明記された外部ツールの仕様・語彙・形式 | 公式資料の範囲だけを記録する |
| `Inference` | 製品の候補構成またはアダプター設計上の帰結 | 対応宣言に昇格させない |
| `Assumption` | P0 probeで検証するための合成条件・仮置き | 実部品の物理値や許容値へ流用しない |
| `UNVERIFIED` | ローカル実機、実行、結果、読込、独立照合が未確認 | `verified`、成功、完成とは呼ばない |
| `Synthetic reference` | 計画書にある解析的な合成参照値 | FEBioの計算結果とは分けて記録する |

調査では実CAEモデル、実結果、認証情報、デスクトップ状態を取得・変更していない。公式ページの確認は外部資料の調査であり、ローカルのインストール、実FEBio、公式FBS、Studio、実モデルのE2Eを実行したものではない。

## 3. 公式資料の台帳

| ID | 公式資料 | `Published` として確認した範囲 | この資料だけでは確認できないこと |
|---|---|---|---|
| GMSH | [Gmsh 4.15.2 Reference Manual](https://gmsh.info/doc/texinfo/gmsh.html) | OpenCASCADEによるSTEP/IGES取込、`SetFactory("OpenCASCADE")`、`ShapeFromFile`、`Geometry.OCCTargetUnit`、順序付き要素節点、要素型11の10節点二次四面体、要素プロパティ・面・辺のAPI | FEBio用のTet10節点順、面順、FEBio入力生成、ローカル実体の適合性 |
| FBIO-CLI | [FEBio User Manual 4.9 — Command line options](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-2.3.html) | `febio4`の`-i`入力、`-p` plot、`-o` log、`-silent`、`-noappend`、`-norun`等。`-p`は主結果のバイナリplot、`-o`はlogとして説明される | 実際に登録された実行ファイル、版・ハッシュ、引数の受理、終了コード、出力の完全性 |
| FBIO-XPLT | [FEBio User Manual 4.9 — Appendix D](https://help.febio.org/docs/FEBioUser-4-9/UM49-Appendix-D.html) と [D.1](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-D.1.html)、[D.1.1](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-D.1.1.html)、[D.2](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-D.2.html)、[D.2.1](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-D.2.1.html)、[D.2.2](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-D.2.2.html)、[D.3](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-D.3.html)、[D.3.1](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-D.3.1.html)、[D.3.2](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-D.3.2.html)、[D.4](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-D.4.html)、[D.5](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-D.5.html)、[D.5.2](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-D.5.2.html) | フォーマット仕様v3.0（D.1.1）、先頭DWORDのFEBio file identifier/magic `0x00464542`（sizeなし、D.2.2）、その後の`ROOT` block `0x01000000`、通常blockのID+size+payload（D.2.1）、`ROOT/MESH/STATE`階層、header/dictionary、型・storage format、node/domain/surface/globalの配置、stateデータ、圧縮フラグの位置づけ | readerの実装、圧縮decoder、版差分、エンディアン・破損・切断への拒否、物理単位・符号、要求変数の意味 |
| FBIO-FEATURE | [FEBio 4.13 Feature Manual](https://febiosoftware.github.io/febio-feature-manual/) の [isotropic elastic](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_isotropic_elastic/)、[neo-Hookean](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_neo-hookean/)、[rigid body](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_rigid_body/)、[prescribed displacement](https://febiosoftware.github.io/febio-feature-manual/features/solid_bc_prescribed_displacement/)、[rigid_displacement](https://febiosoftware.github.io/febio-feature-manual/features/solid_bc_rigid_displacement/)、[sliding-elastic](https://febiosoftware.github.io/febio-feature-manual/features/solid_surfaceinteraction_sliding-elastic/)、[plot variables](https://febiosoftware.github.io/febio-feature-manual/plotvars/) | 候補の入力type、代表パラメータ、node BC、rigid BC、Sliding-Elastic、代表的なplot変数の公式語彙 | 4.13の語彙が登録環境で受理されること、要素・接触・拘束との組合せ、数値品質、readerでの意味の復元 |
| STUDIO | [FEBio Studio 2.8 — The Post Environment UI](https://help.febio.org/docs/FEBioStudio-2-8/FSM28-Section-13.1.html) | plot読込後にPost環境へ移り、Model/Buildを隠し、View/Material/Data/StateのPost機能を示す | ローカルStudioの起動、対象XPLTの読込、最終stateと必要変数の表示、確認証拠の取得方法 |
| RELEASE | [FEBio official releases](https://github.com/febiosoftware/FEBio/releases) | plot形式がAppendix Dで説明されること、`noappend`や圧縮関連のリリース差分があること | 現在の登録版、ローカルでの版差分の影響、readerの互換性 |

資料の版がGmsh 4.15.2、FEBio User Manual 4.9、Feature Manual 4.13、Studio 2.8に分かれている。したがって、資料を横断した「最新環境での組合せ互換性」は資料からは導けない。

## 4. 互換性対応表

| 領域 | 公式資料から言えること | 製品アダプターで固定すべき境界 | 判定 |
|---|---|---|---|
| STEP/IGES → Gmsh | OpenCASCADE経由で形状を読み込める。`ShapeFromFile`は高次元のtagを取得する。`Geometry.OCCTargetUnit="M"`による座標単位変換の説明がある | 元ファイルの単位、変換前後bbox、body/solid数、最高次元tag、面集合、変換の記録。単位未確定を推測しない | `Published` / ローカル利用は`UNVERIFIED` |
| Gmsh Tet10 | MSH type 11は10節点二次四面体。Gmshは順序付きnode listと要素・面・辺の局所順を扱う | FEBioの要素語彙、節点順、面節点順、面法線、要素ID、集合、曲面近似、退化、Jacobianを独立に検証する。Gmshの順序をそのままFEBio順とみなさない | `Published` / 変換は`UNVERIFIED` |
| 等方線形弾性 | `isotropic elastic`とE/vの公式語彙がある。小ひずみ線形への還元と大ひずみ適用上の注意が説明される | E、v、単位、domain、解析設定、出力変数、参照解との誤差、適用ひずみ範囲をprofileとして登録する | `Published` / 組合せは`UNVERIFIED` |
| 圧縮性neo-Hookean | `neo-Hookean`のtype、密度/E/v、超弾性としての説明がある。非圧縮に近い条件の注意がある | パラメータ変換、単純変形の解析参照、エネルギー・応力の整合、収束、出力測度を確認する | `Published` / 組合せは`UNVERIFIED` |
| 剛体治具 | `rigid body`、密度、E/v、COM等の語彙がある。初期自由度と接触用の設定が説明される | 独立メッシュ、剛体material参照、並進・回転6自由度の拘束・処方・自由の解決、COM、剛体position/force/torqueの履歴、部品とのnode共有禁止を確認する | `Published` / 実行は`UNVERIFIED` |
| node_setの処方変位 | `prescribed displacement`はnode_setに対するnodal displacement BCで、DOF、value、relativeの語彙がある。node reactionが必要な場合はzeroでもこのBCを使うと説明される | `<bc type="prescribed displacement" node_set="...">`と`zero displacement`を区別し、nodeの変位、node reaction、符号、load controller、押込み深さを既知解で確認する。これは剛体運動BCの根拠に拡張しない | `Published` / 実行は`UNVERIFIED` |
| rbの剛体運動BC | `rigid_displacement`は`rb`で指定したrigid materialの剛体DOFを処方し、`dof`、`value`、`relative`を持つ。剛体の回転・固定は別のrigid BC語彙で扱う | `<rigid_bc type="rigid_displacement">`と`rb`、並進3DOF、回転3DOF、固定条件、rigid position/force/torqueの所属・符号をnode BCと別probeで確認する | `Published` / 実行は`UNVERIFIED` |
| 接触 | `sliding-elastic`はfacet-on-facetの滑り・摩擦接触。`fric_coeff`、tension、ALM/penalty等の語彙がある | primary/secondary、法線、摩擦、penalty等、初期gap、食い込み、力の釣り合い、摩擦の滑り・固着を実行で確認する | `Published` / 組合せは`UNVERIFIED` |
| 出力変数 | 変位、応力、Lagrange strain、nodal strain/stress、reaction、rigid position/force/torque、contact area/force/gap/pressure/status/traction等の候補がある | 実版の正確な名前、global/node/domain/surface、NODE/ITEM/MULT、状態番号、積分点と節点、単位、符号をreader契約へ固定する | `Published` / 必須変数は`UNVERIFIED` |
| CLI実行 | `-i`、`-p`、`-o`等の公式CLI語彙がある。`-noappend`等の出力管理オプションがある | 絶対パス、版・ハッシュ、引数配列、cwd、終了コード、log、plotの所属・freshness、stale/truncated/改変拒否を記録する | `Published` / ローカルは`UNVERIFIED` |
| XPLT構造 | フォーマット仕様v3.0、先頭file identifier/magic `0x00464542`（sizeなし）、`ROOT` block `0x01000000`、通常blockのID+size+payload、header/dictionary、mesh/state、storage format、圧縮フラグ等が説明される | magicと通常のROOT blockを別段階で読み、エンディアン、block長、dictionary、圧縮/非圧縮、mesh/state、region、storage、状態、必要変数を専用readerで解釈し、未知形式を拒否する | `Published` / readerは`UNVERIFIED` |
| XPLTの物理意味 | XPLTの構造・変数配置は説明される | ケースの単位・測度・符号をmanifestと同じ試行に結び付け、logまたは解析解と独立照合する。平滑化値を評価値へ無断転用しない | `Inference` / `UNVERIFIED` |
| Studio preview | plot読込後のPost環境と表示領域が公式説明される | `LAUNCHED`と`CONFIRMED`を分離し、XPLT hash、Studio版、最終state、必要変数の視認証拠を記録する | `Published` / 実機は`UNVERIFIED` |

## 5. 合成probeの実施順序

以下は次の実装・検証作業へ渡すprobe計画である。本記録では実施していない。各probeは、実行backend、生成物のhash、stdout/stderr、終了コード、判定、未検証項目を別々に保存する。生成したCAD、FEBio、XPLTはGitへ追跡しない。

### 5.1 環境同定

1. 登録されたGmsh、FEBio、FEBio Studioの絶対パス、版表示、実行ファイルhash、関連plugin/library、実行機を取得する。
2. 公式CLI例に合わせ、`febio4 -info -norun`で版情報を取得し、最小入力、`-norun`、`-noappend`の受理を個別に記録する。これは版表示・入力検査のprobeであり、解析成功ではない。
3. どれか一つでも実体、版、hash、登録根拠が欠ける場合、後続の能力を`verified`にしない。

### 5.2 Gmsh形状・単位probe

[Gmsh 4.15.2 OpenCASCADE API](https://gmsh.info/doc/texinfo/gmsh.html#Namespace-gmsh_002fmodel_002focc)に記載された`addSphere`、`addCylinder`、`addBox`を、互いに独立した小さい合成probeとして使用する。以下の寸法はmmを想定した`Assumption`/`Synthetic reference`であり、実生成・実測結果ではない。

| probe | API入力（nominal mm） | 期待する姿勢・bbox | 解析的な期待体積 |
|---|---|---|---|
| 球 | `addSphere(0, 0, 0, 10)`。中心 `(0,0,0)`、半径10 | 任意方向で同じ。`[-10,10] × [-10,10] × [-10,10]` | `4/3*pi*10^3 = 4000*pi/3 ≈ 4188.790 mm³` |
| 円柱 | `addCylinder(0, 0, 0, 0, 0, 20, 5)`。底面中心 `(0,0,0)`、軸ベクトル `(0,0,20)`、半径5 | z軸方向。`[-5,5] × [-5,5] × [0,20]` | `pi*5^2*20 = 500*pi ≈ 1570.796 mm³` |
| 直方体 | `addBox(0, 0, 0, 10, 20, 30)`。基点 `(0,0,0)`、x/y/z方向の辺長 `(10,20,30)` | 座標軸方向。`[0,10] × [0,20] × [0,30]` | `10*20*30 = 6000 mm³` |

1. 各形状を別tag・別modelとして生成し、元の入力寸法、位置、軸または姿勢、最高次元entity、solid/body数、bbox、体積、面集合を記録する。形状同士をfuseせず、治具と部品を結合・節点共有しない。
2. OpenCASCADE経路で得られた最高次元entity、solid/body数、面集合、bbox、向きを記録する。
3. 元単位と`Geometry.OCCTargetUnit`の変換を既知寸法で照合する。例えばmm入力をmへ変換する場合、bboxの各長さを`1e-3`倍、体積を`1e-9`倍として期待値を再計算し、単位不明の入力は停止する。
4. 生成メッシュのelement type、node list、entity orientation、face/edge node listをAPI結果と保存する。APIの存在・引数仕様を確認したことは、ローカル版での実生成成功を意味しない。

### 5.3 Tet10・面・Jacobian probe

1. MSH type 11を含む既知の単一Tet10を生成し、Gmshのnode orderingと局所座標を取得する。
2. 製品側の独立した二次四面体形状関数、節点順変換、面節点順、面法線、signed volume、複数点のJacobianを照合する。
3. 正常、節点入替え、面反転、退化、負Jacobian、集合からの孤立・重複をそれぞれ拒否または判定する。
4. FEBioへ渡す順序は、Gmshの公式順序の転載ではなく、FEBioで受理されることと独立幾何照合の両方で固定する。

### 5.4 材料・既知解probe

計画書の[5.1 均一ひずみの弾性パッチ試験](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md#51-均一ひずみの弾性パッチ試験)を用いる。これは製品の合成検証条件であり、実部品の材料値ではない。圧縮軸をxとすると、期待する均一場は軸ひずみ `-delta/L = -0.001`、Poisson横ひずみ `+nu*delta/L = +0.0003` であり、底面全面固定をこの解析解の代用にしない。単軸応力と整合する境界を全体で定義し、代表応力と反力の相対誤差1%以内、変位場と向きの一致を別々に判定する。

| 条件 | 値・参照 |
|---|---|
| 材料 | E = 1 MPa、nu = 0.3 |
| 形状・変位 | L = 10 mm、A = 100 mm²、delta = 0.01 mm |
| 独立参照 | `F = E A delta / L = 0.100 N` |
| 均一場の参照 | 軸ひずみ `-0.001`、横ひずみ `+0.0003`、軸応力の大きさ `E*delta/L = 0.001 MPa` |
| 適用範囲 | 小ひずみ、等方線形弾性、均一場、接触なし。底面全面固定で代用しない |
| 合格基準 | 反力・代表応力の相対誤差1%以内、変位場と向きの一致 |

FEBioの最終反力が0.100 Nになることだけで合格とせず、入力語彙、支持、変位、変位量、反力符号、要求出力、log/XPLTの一致を同時に確認する。

次に、単純変形のneo-Hookean patchを用い、エネルギー・応力の解析参照、微小ひずみでの極限、出力測度、収束を確認する。数値参照を定められない組合せは能力を宣言しない。

### 5.5 剛体・処方変位・反力probe

候補語彙と対象を混同しないため、node_set向けBCとrb向け剛体BCを別probeにする。

| 対象 | 候補語彙 | 対象・主な確認値 |
|---|---|---|
| 部品のnode_set | `prescribed displacement`、`<bc type="prescribed displacement" node_set="...">` | nodeのDOF、`value`、`relative`、zeroと未指定、node reaction、変位場、反力符号。node reactionが必要な場合はzeroでもprescribed displacementを使うという公式説明を検証する |
| 剛体material | `rigid_displacement`、`<rigid_bc type="rigid_displacement">`、`rb`、`dof`、`value`、`relative` | rigid materialに結び付いた並進x/y/z、剛体position、rigid force/torque、所属・符号。これはnode_setのprescribed displacementとは別の語彙である |
| 剛体の回転・固定 | `rigid_euler_angles`、`rigid_rotation`、`rigid_fixed`等の対応版語彙 | 回転x/y/z、固定・自由の6DOF、COM、rigid position/force/torque。正確な受理語彙と出力所属はnative probeで確認する |

1. 独立メッシュの剛体治具を用い、並進3・回転3自由度を、node_set BC、rigid BC、固定、自由の各意味に分けて明示する。
2. node_setの既知変位probeではzero、relative、load controller、node reaction、最終位置を確認する。剛体の既知運動probeでは`rigid_displacement`の`rb`、`dof`、`value`、`relative`、rigid position/force/torqueを確認する。
3. 部品のprescribed node reactionと剛体のrigid force/torqueを別の出力として、全体の力・モーメントの釣り合いとともに比較する。node向けの「反力取得にはprescribed displacementが必要」という説明を、剛体反力へ無条件に拡張しない。
4. COM指定と自動COM、回転固定、部品とのnode共有禁止を含む境界を個別に確認する。公式語彙を資料から確認しただけでは、登録版での実行や数値符号を`verified`にしない。

### 5.6 接触・摩擦probe

1. 剛体球と有限試験片の摩擦なし接触を用いる。計画書の解析参照は `R = 10 mm`、`delta = 0.01 mm`、E = 1 MPa、nu = 0.3、参照力 `0.00463 N` である。
2. 初期接触、gap、接触面の向き、primary/secondary、非共有node、食い込み、反力符号を確認する。
3. 試験片寸法を拡大した反力差1%以内、接触近傍を連続細分化した反力差各2%以内、Hertz参照誤差5%以内を別の判定として記録する。
4. 摩擦係数zeroと正の係数を分け、接触圧が正の区間で滑り・固着とCoulomb限界、力の釣り合いを確認する。
5. `sliding-elastic`のpenalty/ALM等の設定は、実版で受理された語彙と数値効果を対応表へ登録する。

Hertz参照は小変形・弾性・小接触領域・十分大きい試験片の理想化であり、摩擦や一般の大変形を保証しない。合成probeの結果を実部品の許容値、材料同定、解析品質へ流用しない。

### 5.7 XPLT reader probe

1. 同一の合成実行から、非圧縮と圧縮のXPLTを取得できる場合は両方を保存し、取得できない形式は未対応として記録する。
2. フォーマット仕様v3.0とFEBio User Manual 4.9を混同しない。まず先頭DWORDのfile identifier/magic `0x00464542`をsizeなしで検証し、その直後に通常blockのsizeを読まない。次に`ROOT` block `0x01000000`を通常blockとして検証し、以降の通常blockをID+size+payloadとして読む。
3. `ROOT` block、version、header、dictionary、mesh、state、block長、endian、compression、region、storage format、node/domain/surface/global、state番号を段階的に検証する。
4. 必須の変位、応力・ひずみ、剛体履歴、接触診断の存在、型、配置、状態、単位、符号を確認する。
5. 切断、長さ改変、dictionary改変、古いrunのファイル差替え、hash不一致、未知version、未知compressionを拒否する。
6. readerの数値は、同じ試行のlog、単純な解析参照、力の釣り合い、要素積分または独立計算と照合する。readerが読めたことだけを正しさの証拠にしない。

XPLT Appendix Dの記述から圧縮decoderの互換性を推論してはならない。圧縮実データを取得し、版・hashを固定した実行でreaderを検証するまで、圧縮対応は`UNVERIFIED`である。

### 5.8 Studio launch/read probe

1. 登録済みStudioを、対象XPLTのhashと試行manifestを明示して起動する。
2. プロセス起動を`LAUNCHED`、対象XPLT・最終state・変位等の必要変数が実際に表示できた確認を`CONFIRMED`として分ける。
3. 読込確認には、XPLT hash、Studio版・hash、対象state、表示した変数、確認者または確認手順、時刻、必要な画面証拠を付ける。
4. 起動できても読込できない、別ファイルを表示した、古いstateを表示した、必要変数がない場合は`FAILED`または`UNVERIFIED`であり、成功扱いにしない。

## 6. 権威Gateとの対応と現状

次表は[実装・検証計画のGate表](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md#4-検証の種類と範囲)のID・種類・意味を保持したものである。P0-Bの文書調査では、これらの権威Gateを新しいprobe名や数値結果で置換しない。`UNEXECUTED`は今回の実行対象外、`UNVERIFIED`は実機・実行・結果の証拠がまだないことを示す。

| 権威Gate | 種類 | 権威計画が確認する振る舞い | 現状 |
|---|---|---|---|
| CT-01 | unit | 物理根拠、単位、schema、対応範囲、必須値、未指定とゼロ | `UNEXECUTED` |
| CT-02 | unit/component | 草案世代、質問の一度だけの適用、親版一致、登録からの実行権限 | `UNEXECUTED` |
| CT-03 | component | 原子的保存、二重起動防止、リンク・パス境界、失敗後の復旧 | `UNEXECUTED` |
| GM-01 | component/native | STEP単位・ボディ選択と、3種類の剛体生成の寸法・姿勢 | `UNVERIFIED` |
| GM-02 | component/native | 領域、節点順、面方向、Tet10、接触面の非共有、品質 | `UNVERIFIED` |
| GM-03 | component/native | 再メッシュの領域継承、形状変更での再解決、キャッシュ改変検出 | `UNVERIFIED` |
| FB-01 | component/native | 材料・剛体・支持・接触・運動・出力要求のFEBio変換 | `UNVERIFIED` |
| FB-02 | component/native | 実行の所有、子孫の排出、停止、タイムアウト、残存プロセス | `UNVERIFIED` |
| FB-03 | component/native | stale/truncated/改変出力の拒否、XPLT必須変数・状態、反力符号 | `UNVERIFIED` |
| QA-01 | native | 合成の既知解・接触参照解・メッシュ依存性・釣り合い | `UNVERIFIED` |
| VW-01 | native/manual | 公式Studioで対象XPLT、最終状態、変位等の読込・表示確認 | `UNVERIFIED` |
| AI-01 | component | 自然言語からの提案、根拠不明時の質問、LLM出力のスキーマ・権限検証 | `UNEXECUTED` |
| AI-02 | native | 実際のLLM接続から日本語意図→草案→質問→版確定までの経路 | `UNEXECUTED` |
| RV-01 | component/native | 元版保存、型付き差分、再解析、変更依存性と予算 | `UNEXECUTED` |
| CP-01 | component/native | 共通移動量、単位・領域・測度、意図した差分、ゼロ基準値、補間範囲 | `UNEXECUTED` |
| PK-01 | local/installed | 全ローカルゲート、wheelの起動、インストール先からのimport | `UNEXECUTED` |
| E2E-01 | installed/native | 合成STEP→実FEBio→検証→Studio→部分変更→比較 | `UNVERIFIED` |
| E2E-02 | installed/real model | 許可された実STEPの同じ全経路 | `UNVERIFIED` |
| E2E-03 | installed/real model | 最終BottomFrameケースの全経路と指定の品質・比較 | `UNVERIFIED` |

P0-Bで使う独自probe識別子は、権威Gateの代わりではなく、対応する部分証拠を追跡するためのローカル名である。

| 独自probe ID | 対応する権威Gate | このprobeで示す部分 | このprobe単独で満たさないもの |
|---|---|---|---|
| `P0B-ENV-01` | GM-01、FB-01、VW-01、PK-01 | Gmsh/FEBio/Studioの実体・版・hash・登録根拠 | 各Gateの実行、ライフサイクル、wheel全経路 |
| `P0B-GM-CAD-01` | GM-01 | 3種類のOpenCASCADE primitiveの寸法・姿勢・bbox・体積と単位期待値 | STEPの実ボディ選択、実Gmsh、3種生成の製品変換 |
| `P0B-GM-TET10-01` | GM-02 | Tet10節点・面順、面方向、Jacobian、非共有接触面の独立チェック | 再メッシュ・cache、FEBio受理、全GM-02品質 |
| `P0B-FB-FEATURE-01` | FB-01 | 材料・剛体・node BC・rigid BC・接触の候補語彙の対応 | 登録版での受理、実変換、実行、出力の正しさ |
| `P0B-FB-NODE-BC-01` | FB-01、FB-03、QA-01 | node_setのprescribed displacement、node reaction、zero/relativeの部分確認 | 剛体の6DOF、process ownership、XPLT必須変数全体 |
| `P0B-FB-RB-BC-01` | FB-01、QA-01 | `rigid_displacement`、`rb`、剛体位置・力・トルク、回転・固定の部分確認 | node BC、FB-02の停止・drain・timeout、全FB-01 |
| `P0B-FB-XPLT-01` | FB-03、QA-01 | magic/ROOT、dictionary、mesh/state、圧縮、要求変数、独立数値照合の部分確認 | stale/truncated/改変出力の全拒否、実readerの版互換性 |
| `P0B-QA-ELASTIC-01` | QA-01 | 均一場、Poisson収縮、反力・代表応力の1%参照 | 実native解析、FB-02、P0全体 |
| `P0B-QA-HERTZ-01` | QA-01 | Hertz反力、境界・メッシュ依存性、摩擦なし接触の部分確認 | 実native解析、摩擦profile、E2E-01/02/03 |
| `P0B-QA-PROFILE-01` | FB-01、QA-01 | 摩擦あり、neo-Hookean、接触・剛体運動のprofile別基準 | 権威Gateの全入力・process・reader証拠 |
| `P0B-VW-STUDIO-01` | VW-01 | 起動と対象XPLT・最終state・必要変数の読込確認計画 | 実Studio読込、E2E-01、E2E-02、E2E-03 |

上表の独自probeはすべて現状未実施であり、対応する権威Gateを合格扱いにしない。特にFB-02は数値結果ではなく、実行所有権、子孫プロセス排出、停止、timeout、残存プロセスの証拠が必要である。E2E-02は許可された一般real STEP、E2E-03は最終BottomFrameであり、同じ行や同じ証拠へ混合しない。P0-BはP3/P6/P7の完了を宣言せず、P0 probeの合格を製品経路の成功、実FEBio/FBSの成功、Studio確認、実モデルE2Eの成功へ置き換えない。

独立した合成モデルを実FEBioで実行できた場合は、モデルの出所、実行backend、結果reader、viewerを別項目に記録した`native probe`として報告する。それは実FEBioの合成probe証拠であり、製品経路、公式FBS、E2E-01、許可された実モデル、E2E-03を完了したことを意味しない。

## 7. 実施記録の境界

### 実施したこと

- 製品の2権威文書からP0互換性要求、合成参照値、未検証の扱いを確認した。
- Gmsh、FEBio User/Feature、FEBio Studioの公式資料を版付きで調査した。
- 公式資料の語彙と、製品アダプターが独自に検証すべき境界を対応表へ整理した。
- 次の合成probe、独立数値照合、Studio確認、権威Gateとの対応を定義した。

### 実施していないこと

- Gmsh、FEBio、FEBio Studioのローカル起動、版表示、hash取得。
- 実FEBio、公式FBS、FEBio Studio、実モデル、`02_CAE`データの実行・読込・変更。
- GmshからFEBioへのTet10節点順・面順の実測変換。
- XPLTの実ファイル取得、reader実装、圧縮decoder、改変・切断拒否。
- 合成弾性、Hertz、摩擦、neo-Hookeanのネイティブ解析と数値照合。
- Studioの起動、対象XPLTの読込確認、画面証拠の収集。
- 製品実装、製品pytestのRED/GREEN、ruff、mypy、CAE境界スキャナー、build、installed smoke。

従って、本記録には実測の合格件数、実行時間、性能値、解析結果はない。次タスクは、P0の実装・検証担当が独自probe IDを実環境で実施し、対応する権威Gate（GM-01/02、FB-01/02/03、QA-01、VW-01、必要なPK-01/E2E-01等）ごとに、実体・版・hash・コマンド・件数・終了コード・未検証項目を固定することである。P0-Bの文書判定は独立レビュー待ちであり、P0・P3・P6・P7やE2E-02/E2E-03の完了を宣言しない。
