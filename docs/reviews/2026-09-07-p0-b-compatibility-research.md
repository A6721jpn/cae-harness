# P0-B 公式互換性調査記録

日付: 2026-09-07
対象: FEBio CAE Harness V2 の P0-B 公式互換性調査
対象コミット: `0cc803a6c5a568172662592e5ec932b9c7d09112`
ブランチ: `codex/p0-b-compatibility-research`
remote: `https://github.com/A6721jpn/cae-harness.git`（`REMOTE_CONFIGURED`）

## 1. 結論

公式マニュアルから、候補経路の語彙・形式・検証すべき境界は整理できた。しかし、これはネイティブ互換性の合格証拠ではない。Gmsh、FEBio、FEBio Studioのローカル実体の絶対パス・版・ハッシュ、実行結果、XPLT読込、節点順変換、数値照合、Studioでの対象XPLT読込は本調査では未実施である。

したがって、本記録の判定は次のとおりとする。

> `P0-B DOCUMENTARY BASELINE COMPLETE; NATIVE COMPATIBILITY UNVERIFIED; P0 NOT READY`

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
| FBIO-XPLT | [FEBio User Manual 4.9 — Appendix D](https://help.febio.org/docs/FEBioUser-4-9/UM49-Appendix-D.html) と [D.1](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-D.1.html)、[D.1.1](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-D.1.1.html)、[D.2](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-D.2.html)、[D.2.1](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-D.2.1.html)、[D.2.2](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-D.2.2.html)、[D.3](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-D.3.html)、[D.3.1](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-D.3.1.html)、[D.3.2](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-D.3.2.html)、[D.4](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-D.4.html)、[D.5](https://help.febio.org/docs/FEBioUser-4-9/UM49-Section-D.5.html)、[D.5.2](https://help.febio.org/docs/FEBioUser-4-9/UM49-Subsection-D.5.2.html) | v3.0、自記述・拡張可能な構造、`0x00464542` root tag、`ROOT/MESH/STATE`階層、header/dictionary、型・storage format、node/domain/surface/globalの配置、stateデータ、圧縮フラグの位置づけ | readerの実装、圧縮decoder、版差分、エンディアン・破損・切断への拒否、物理単位・符号、要求変数の意味 |
| FBIO-FEATURE | [FEBio 4.13 Feature Manual](https://febiosoftware.github.io/febio-feature-manual/) の [isotropic elastic](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_isotropic_elastic/)、[neo-Hookean](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_neo-hookean/)、[rigid body](https://febiosoftware.github.io/febio-feature-manual/features/solid_material_rigid_body/)、[prescribed displacement](https://febiosoftware.github.io/febio-feature-manual/features/solid_bc_prescribed_displacement/)、[sliding-elastic](https://febiosoftware.github.io/febio-feature-manual/features/solid_surfaceinteraction_sliding-elastic/)、[plot variables](https://febiosoftware.github.io/febio-feature-manual/plotvars/) | 候補の入力type、代表パラメータ、処方変位、Sliding-Elastic、代表的なplot変数の公式語彙 | 4.13の語彙が登録環境で受理されること、要素・接触・拘束との組合せ、数値品質、readerでの意味の復元 |
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
| 剛体治具 | `rigid body`、密度、E/v、COM等の語彙がある。初期自由度と接触用の設定が説明される | 独立メッシュ、6自由度の拘束・処方・自由の解決、COM、反力・トルクの履歴、部品とのnode共有禁止を確認する | `Published` / 実行は`UNVERIFIED` |
| 処方変位 | `prescribed displacement`、DOF、value、relativeの語彙がある。反力取得には処方変位が必要と説明される | 変位の符号、zeroと未指定の区別、load controller、押込み深さ、反力符号を既知解で確認する | `Published` / 実行は`UNVERIFIED` |
| 接触 | `sliding-elastic`はfacet-on-facetの滑り・摩擦接触。`fric_coeff`、tension、ALM/penalty等の語彙がある | primary/secondary、法線、摩擦、penalty等、初期gap、食い込み、力の釣り合い、摩擦の滑り・固着を実行で確認する | `Published` / 組合せは`UNVERIFIED` |
| 出力変数 | 変位、応力、Lagrange strain、nodal strain/stress、reaction、rigid position/force/torque、contact area/force/gap/pressure/status/traction等の候補がある | 実版の正確な名前、global/node/domain/surface、NODE/ITEM/MULT、状態番号、積分点と節点、単位、符号をreader契約へ固定する | `Published` / 必須変数は`UNVERIFIED` |
| CLI実行 | `-i`、`-p`、`-o`等の公式CLI語彙がある。`-noappend`等の出力管理オプションがある | 絶対パス、版・ハッシュ、引数配列、cwd、終了コード、log、plotの所属・freshness、stale/truncated/改変拒否を記録する | `Published` / ローカルは`UNVERIFIED` |
| XPLT構造 | v3.0、header/dictionary、root tag、mesh/state、storage format、圧縮フラグ等が説明される | エンディアン、block長、dictionary、圧縮/非圧縮、mesh/state、region、storage、状態、必要変数を専用readerで解釈し、未知形式を拒否する | `Published` / readerは`UNVERIFIED` |
| XPLTの物理意味 | XPLTの構造・変数配置は説明される | ケースの単位・測度・符号をmanifestと同じ試行に結び付け、logまたは解析解と独立照合する。平滑化値を評価値へ無断転用しない | `Inference` / `UNVERIFIED` |
| Studio preview | plot読込後のPost環境と表示領域が公式説明される | `LAUNCHED`と`CONFIRMED`を分離し、XPLT hash、Studio版、最終state、必要変数の視認証拠を記録する | `Published` / 実機は`UNVERIFIED` |

## 5. 合成probeの実施順序

以下は次の実装・検証作業へ渡すprobe計画である。本記録では実施していない。各probeは、実行backend、生成物のhash、stdout/stderr、終了コード、判定、未検証項目を別々に保存する。生成したCAD、FEBio、XPLTはGitへ追跡しない。

### 5.1 環境同定

1. 登録されたGmsh、FEBio、FEBio Studioの絶対パス、版表示、実行ファイルhash、関連plugin/library、実行機を取得する。
2. FEBioのCLIで`-info`相当の版表示、最小入力、`-norun`、`-noappend`の受理を個別に記録する。
3. どれか一つでも実体、版、hash、登録根拠が欠ける場合、後続の能力を`verified`にしない。

### 5.2 Gmsh形状・単位probe

1. 既知寸法の合成primitiveと、許可された合成STEPをGmshへ入力する。
2. OpenCASCADE経路で得られた最高次元entity、solid/body数、面集合、bbox、向きを記録する。
3. 元単位と`Geometry.OCCTargetUnit`の変換を既知寸法で照合し、単位不明の入力は停止する。
4. 生成メッシュのelement type、node list、entity orientation、face/edge node listをAPI結果と保存する。

### 5.3 Tet10・面・Jacobian probe

1. MSH type 11を含む既知の単一Tet10を生成し、Gmshのnode orderingと局所座標を取得する。
2. 製品側の独立した二次四面体形状関数、節点順変換、面節点順、面法線、signed volume、複数点のJacobianを照合する。
3. 正常、節点入替え、面反転、退化、負Jacobian、集合からの孤立・重複をそれぞれ拒否または判定する。
4. FEBioへ渡す順序は、Gmshの公式順序の転載ではなく、FEBioで受理されることと独立幾何照合の両方で固定する。

### 5.4 材料・既知解probe

計画書の合成弾性patchを用いる。これは製品の合成検証条件であり、実部品の材料値ではない。

| 条件 | 値・参照 |
|---|---|
| 材料 | E = 1 MPa、nu = 0.3 |
| 形状・変位 | L = 10 mm、A = 100 mm²、delta = 0.01 mm |
| 独立参照 | `F = E A delta / L = 0.100 N` |
| 適用範囲 | 小ひずみ、等方線形弾性、均一場、接触なし |

FEBioの最終反力が0.100 Nになることだけで合格とせず、入力語彙、支持、変位、変位量、反力符号、要求出力、log/XPLTの一致を同時に確認する。

次に、単純変形のneo-Hookean patchを用い、エネルギー・応力の解析参照、微小ひずみでの極限、出力測度、収束を確認する。数値参照を定められない組合せは能力を宣言しない。

### 5.5 剛体・処方変位・反力probe

1. 独立メッシュの剛体治具を用い、並進3・回転3自由度の各状態を明示する。
2. prescribed displacementを用いた既知運動で、zero、relative、load controller、最終位置を区別する。
3. rigid position/force/torque、prescribed node reaction、全体の力の釣り合いを比較する。
4. 処方変位がないと反力を取得できないケース、COM指定と自動COM、部品とのnode共有を含む境界を個別に確認する。

### 5.6 接触・摩擦probe

1. 剛体球と有限試験片の摩擦なし接触を用いる。計画書の解析参照は `R = 10 mm`、`delta = 0.01 mm`、E = 1 MPa、nu = 0.3、参照力 `0.00463 N` である。
2. 初期接触、gap、接触面の向き、primary/secondary、非共有node、食い込み、反力符号を確認する。
3. 試験片寸法を拡大した反力差1%以内、接触近傍を連続細分化した反力差各2%以内、Hertz参照誤差5%以内を別の判定として記録する。
4. 摩擦係数zeroと正の係数を分け、接触圧が正の区間で滑り・固着とCoulomb限界、力の釣り合いを確認する。
5. `sliding-elastic`のpenalty/ALM等の設定は、実版で受理された語彙と数値効果を対応表へ登録する。

Hertz参照は小変形・弾性・小接触領域・十分大きい試験片の理想化であり、摩擦や一般の大変形を保証しない。合成probeの結果を実部品の許容値、材料同定、解析品質へ流用しない。

### 5.7 XPLT reader probe

1. 同一の合成実行から、非圧縮と圧縮のXPLTを取得できる場合は両方を保存し、取得できない形式は未対応として記録する。
2. root tag、version、header、dictionary、mesh、state、block長、endian、compression、region、storage format、node/domain/surface/global、state番号を段階的に検証する。
3. 必須の変位、応力・ひずみ、剛体履歴、接触診断の存在、型、配置、状態、単位、符号を確認する。
4. 切断、長さ改変、dictionary改変、古いrunのファイル差替え、hash不一致、未知version、未知compressionを拒否する。
5. readerの数値は、同じ試行のlog、単純な解析参照、力の釣り合い、要素積分または独立計算と照合する。readerが読めたことだけを正しさの証拠にしない。

XPLT Appendix Dの記述から圧縮decoderの互換性を推論してはならない。圧縮実データを取得し、版・hashを固定した実行でreaderを検証するまで、圧縮対応は`UNVERIFIED`である。

### 5.8 Studio launch/read probe

1. 登録済みStudioを、対象XPLTのhashと試行manifestを明示して起動する。
2. プロセス起動を`LAUNCHED`、対象XPLT・最終state・変位等の必要変数が実際に表示できた確認を`CONFIRMED`として分ける。
3. 読込確認には、XPLT hash、Studio版・hash、対象state、表示した変数、確認者または確認手順、時刻、必要な画面証拠を付ける。
4. 起動できても読込できない、別ファイルを表示した、古いstateを表示した、必要変数がない場合は`FAILED`または`UNVERIFIED`であり、成功扱いにしない。

## 6. P0合格ゲートと現状

| ゲート | 合格に必要な証拠 | 現状 |
|---|---|---|
| ENV-01 | Gmsh/FEBio/Studioの絶対パス、版、hash、登録根拠 | `UNVERIFIED` |
| GM-01 | 合成STEP取込、単位、最高次元tag、bbox、面集合の実測 | `UNVERIFIED` |
| GM-02 | Tet10節点・面順、法線、Jacobian、退化・負値の独立検証 | `UNVERIFIED` |
| FB-01 | 候補材料・剛体・支持・接触の実入力、実行、結果 | `UNVERIFIED` |
| FB-02 | 処方変位、反力、剛体位置・力・トルクの既知解と符号 | `UNVERIFIED` |
| FB-03 | XPLT header/dictionary/mesh/state/圧縮/必須変数と改変拒否 | `UNVERIFIED` |
| QA-01 | 弾性patchの0.100 N、接触参照の0.00463 N、収束・釣り合い | `UNVERIFIED` |
| QA-02 | 摩擦なし・摩擦あり・neo-Hookeanのprofile別基準 | `UNVERIFIED` |
| VW-01 | Studioの起動記録と対象XPLTの人による読込確認 | `UNVERIFIED` |
| P0-LOCAL | 指定pytest、ruff、mypy、CAE boundary、build、installed smoke | `UNEXECUTED` |
| E2E-03 | 許可済み実モデルと最終BottomFrameのfreshな全経路 | `UNVERIFIED` |

P0の実装へ進む場合も、合成probeの合格を実製品経路の成功、実FEBio/FBSの成功、Studio確認、実モデルE2Eの成功へ置き換えてはならない。計画書どおり、P0 probeは製品経路の成功に数えず、アダプター実装後のP3・P6で再実行する。

## 7. 実施記録の境界

### 実施したこと

- 製品の2権威文書からP0互換性要求、合成参照値、未検証の扱いを確認した。
- Gmsh、FEBio User/Feature、FEBio Studioの公式資料を版付きで調査した。
- 公式資料の語彙と、製品アダプターが独自に検証すべき境界を対応表へ整理した。
- 次の合成probe、独立数値照合、Studio確認、P0ゲートを定義した。

### 実施していないこと

- Gmsh、FEBio、FEBio Studioのローカル起動、版表示、hash取得。
- 実FEBio、公式FBS、FEBio Studio、実モデル、`02_CAE`データの実行・読込・変更。
- GmshからFEBioへのTet10節点順・面順の実測変換。
- XPLTの実ファイル取得、reader実装、圧縮decoder、改変・切断拒否。
- 合成弾性、Hertz、摩擦、neo-Hookeanのネイティブ解析と数値照合。
- Studioの起動、対象XPLTの読込確認、画面証拠の収集。
- 製品実装、製品pytestのRED/GREEN、ruff、mypy、CAE境界スキャナー、build、installed smoke。

従って、本記録には実測の合格件数、実行時間、性能値、解析結果はない。次タスクは、P0の実装・検証担当がこの記録のENV/GM/FB/QA/VWゲートを実環境で実施し、実体・版・hash・コマンド・件数・終了コード・未検証項目を対応表へ固定することである。
