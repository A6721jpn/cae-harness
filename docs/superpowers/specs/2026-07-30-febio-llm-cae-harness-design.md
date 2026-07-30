# Codex向けFEBio CAE Harness設計

## 1. 文書の位置づけ

- Status: approved concept design
- Date: 2026-07-30
- Target LLM: Codex only
- Tool repository:
  `C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\febio-tools`
- Real CAE workspace:
  `C:\Users\backo\OneDrive\Documents\FEBio\02_CAE`

本設計は、人間が与えたSTEPまたは過去の類似FEBと自然言語指示から、
Codexが解析意図を理解し、FEBioヘッドレス解析を計画、実行、検証、
デバッグ、報告するためのCAE Harnessを定義する。

本システムの目的は、単にFEBioプロセスを起動することではない。
解析意図、人間承認、入力来歴、モデル差分、メッシュ品質、収束、
結果証拠を一つの監査可能なケースとして管理する。

## 2. 基本原則

1. Codexは工学的な意味を解釈し、質問、仮説、修正候補、次の操作を選ぶ。
2. CAE Harness CLIは状態遷移、証拠、パス、承認、安全ゲートを決定論的に
   強制する。
3. FEBioの終了コード、LOGまたはXPLTの存在だけでは解析成功としない。
4. デバッグはエラーを消すことではなく、解析意図を保ったまま有効な解を
   得ることを目的とする。
5. 会話履歴を正式状態にせず、解析意図、判断、承認、attempt、証拠を
   ケース成果物として保存する。
6. 再利用コード、合成fixture、テスト、Skill、文書はGit管理された
   `01_Tools/febio-tools`に置く。
7. 実製品のSTEP、FSM、FEB、XPLT、解析結果、実モデル検証はGitを使用しない
   `02_CAE`だけに置く。
8. 既存CAE成果物を上書きまたは削除しない。

## 3. 入力モード

### 3.1 STEP起点

人間は対象STEPと自然言語指示を与える。STEPを対象形状の正本とし、
Codexが材料、荷重、拘束、接触、解析ステップ、評価量に関する不足情報を
抽出して質問する。

### 3.2 FEB起点

人間は基準または類似FEBと自然言語指示を与える。FEB内の形状、メッシュ、
材料、選択セット、境界条件、荷重、接触、数値制御、出力設定を解析し、
今回の指示との差分を作成する。

### 3.3 STEPと類似FEBの併用

STEPを対象形状の正本、類似FEBを解析知識の候補として扱う。FEB内の面番号、
節点ID、要素IDを直接流用せず、幾何特徴と意味的役割から対象STEPへ
再対応付けする。

すべての入力は改変せず保存または権威ある外部参照として記録し、パス、
サイズ、SHA-256、取得日時をケースマニフェストへ記録する。

## 4. システム構成

採用方式は、解析意図を持つ状態機械型ハーネスとする。

```text
Human
  -> Codex CAE Case Agent
       -> Codex Skill
            -> CAE Harness CLI Core
                 -> STEP/FEB/Gmsh/FEBio/FBS adapters
                 -> deterministic validators
```

### 4.1 Codex CAE Case Agent

Codexは人間との対話窓口であり、次を担当する。

- 解析意図の理解
- 不足情報の質問
- 類似FEBからの継承候補判断
- デバッグ原因仮説
- 修正候補と検証計画
- CLIが許可した次操作の選択
- 人間向け説明と報告

デバッグは無文脈の別エージェントへ移譲しない。同じ解析意図契約を所有する
Case Agentが担当する。補助エージェントを使う場合も、承認済み契約revision、
意味付きエンティティマップ、証拠bundleを入力として渡す。

### 4.2 Codex Skill

SkillはCodexへ次を教える再利用可能なワークフロー層である。

- CLIコマンドと呼出順
- 解析意図契約の作成手順
- 質問、停止、人間承認の条件
- エラー分類ごとの診断手順
- 変更影響の説明方法
- 結果報告形式

Skillの指示だけを安全境界にしない。未承認解析、禁止パス、無効な状態遷移、
証拠不足の昇格はCLIも拒否する。

### 4.3 CAE Harness CLI Core

CLI Coreは正式なシステム状態を管理する。

- ケース状態機械
- 解析意図契約とrevision
- 人間承認記録
- append-onlyイベント
- 入力、設定、実行ファイル、成果物のハッシュ
- attempt分離、ロック、再開
- 許可操作と禁止操作
- リソース予算
- 証拠ゲートと変更影響判定

各コマンドは機械可読JSONを返し、少なくとも次を含む。

```json
{
  "schema_version": 1,
  "status": "success",
  "case_state": "INTENT_APPROVED",
  "blockers": [],
  "allowed_next_actions": [],
  "artifacts": [],
  "evidence": []
}
```

### 4.4 アダプターと検証器

次の単一責任コンポーネントを独立させる。

- STEP geometry inspector
- reference FEB parser
- FEB inheritance checker
- semantic entity mapper
- Gmsh mesher
- FEB model compiler
- FEBio runner
- solver LOG parser
- XPLT/FBS result reader
- geometry/mesh/model/solver/result validators
- intent impact gate
- report generator

CodexはGmshまたは`febio4.exe`を直接呼ばず、CLIのアダプターを経由する。

## 5. 解析意図契約

解析開始前に、人間承認済みの解析意図契約を必須とする。

契約には次を含める。

- 答えたい工学的な問い
- 対象部品と各部品の意味的役割
- 単位系
- 材料、荷重、拘束、接触、解析ステップ
- 関心領域（ROI）と保護すべき形状
- 評価量、対象population、統計量、方向、時刻
- 荷重経路と重要な物理仮定
- 維持すべき不変条件
- 許容する数値変更と上限
- メッシュ収束、保存量、結果差の基準
- 人間承認が必要な変更
- 未確定事項
- 禁止する結論
- 各設定の出典

各設定項目は次の状態のいずれかを持つ。

```text
USER_SPECIFIED
INHERITED
OVERRIDDEN
INFERRED
UNRESOLVED
PROHIBITED
```

### 5.1 情報の優先順位

```text
current explicit user instruction
  -> approved analysis intent contract
  -> compatible base FEB item
  -> similar FEB proposal
  -> approved default
```

契約承認後に新しい指示が契約と衝突した場合、既存契約を上書きせず新revisionを
作り、人間の再承認を要求する。

### 5.2 情報不足のリスク別処理

- 単位、材料、荷重、拘束、接触、評価対象など物理的に重要な不足は停止して
  人間へ質問する。
- ログ設定、成果物名、診断出力などは承認済みデフォルトを使用できる。
- メッシュサイズや数値制御値は、仮定を明示した診断attemptでのみ使用できる。
- 仮定を含む診断attemptを正式結果または設計合否へ使用しない。
- 正式解析へ昇格するには、仮定を契約revisionへ取り込み、人間承認を得る。

## 6. 類似FEBの互換性と継承

類似FEBをファイル単位でコピーしない。次の項目ごとに互換性を検査する。

- FEBioスキーマとバージョン
- 単位系
- 形状、メッシュ、Domain対応
- 材料モデルとパラメータ
- Node/Surface/Element Set
- 荷重、拘束、接触の参照先
- 解析ステップと数値制御
- 必要な出力変数
- 過去解析の検証状態

継承結果は`inheritance-report.json`へ記録し、各項目を`adopted`、
`overridden`、`proposed`、`rejected`、`unresolved`に分類する。

STEPとFEBを併用する場合は、STEPを対象形状の正本とする。FEBだけが与えられた
場合は、FEB内の形状とメッシュを基準入力にできるが、CAD面の意味、単位、
選択セットの意図を確認できない項目は`UNRESOLVED`にする。

意味付きエンティティマップは、CAD特徴、FE集合、材料Domain、荷重、拘束、
接触、ROI、結果populationの対応を保持し、再メッシュ後も解析意図を追跡する。

## 7. ケース状態とデータフロー

### 7.1 正常系

```text
CASE_CREATED
  -> INPUT_INSPECTED
  -> INTENT_DRAFTED
  -> INTENT_APPROVED
  -> MODEL_BUILT
  -> PREFLIGHT_PASSED
  -> SOLVED
  -> RESULT_VERIFIED
  -> REPORTED
  -> HUMAN_ACCEPTED
```

### 7.2 分岐状態

```text
MODEL_FAILED
MESH_REJECTED
SOLVE_FAILED
RESULT_INCOMPLETE
INTENT_IMPACT_REVIEW
WAITING_FOR_HUMAN
CANCELLED
```

状態変更はappend-onlyイベントとして保存し、`CASE_MANIFEST.json`は現在状態を
表す投影データとする。

### 7.3 ケース構成

```text
<Analysis_ID>/
  README.md
  CASE_MANIFEST.json
  01_Input/
  02_Model/
  03_Result/
  04_Report/
  05_Verification/
  90_Temporary/
```

各実行は次の独立attemptを作る。

```text
90_Temporary/attempts/<attempt-id>/
  input-manifest.json
  intent-revision.json
  normalized-config.json
  commands.json
  tool-fingerprints.json
  stage-events.jsonl
  raw-logs/
  generated/
  attempt-result.json
```

入力ハッシュ、契約revision、正規化設定、ツール指紋からresume keyを生成する。
一致する完了stageは再利用できる。いずれかが異なれば新attemptを作る。

検証済み成果物だけをアトミックに永久領域へ昇格する。

- 最終FSM/FEB: `02_Model`
- 完成XPLT: `03_Result`
- 最終レポート: `04_Report`
- solver log、品質、承認、影響評価: `05_Verification`

失敗、中断、INITのみの成果物を完成結果へ昇格しない。

## 8. FEBioヘッドレス実行

FEBio Runnerは次を記録する。

- 実行ファイルのパス、ハッシュ、FEBioバージョン
- 入力FEBと設定のハッシュ
- argv、作業ディレクトリ、許可された環境変数
- PIDと子プロセス
- 開始、終了時刻
- stdout/stderr
- 終了コードと終了理由
- CPU時間、最大メモリ、経過時間
- LOG、XPLT、dumpのサイズとハッシュ

FEBio追加引数は自由入力にせず、許可リストと型付き設定から生成する。

### 8.1 複合成功ゲート

次をすべて満たす場合だけ`SOLVED`とする。

1. プロセスが正常終了した。
2. solver LOGが今回のattemptで新規生成された。
3. LOGがnormal terminationを示す。
4. 期待した全stepと最終timeまで進行した。
5. fatal error、未解決参照、Negative Jacobianがない。
6. XPLTが新規、非空、読込可能である。
7. XPLTの最終state/timeが期待値と一致する。
8. 解析意図で要求した出力変数が存在する。

警告は`RECOVERED_WARNING`、`UNRESOLVED_WARNING`、`FATAL`に分類する。
警告が存在するだけで一律失敗にはしない。

### 8.2 キャンセル、タイムアウト、再開

- キャンセル時は対象attemptのプロセスツリーだけを停止する。
- 部分LOG、XPLT、dumpは保存するが昇格しない。
- ケースとattemptに書込みロックを設定する。
- 同一ケースの書込み処理は直列にする。
- 複数ケースの並列解析はCPUとメモリ予算内に限定する。
- 初期版では中断したsolveをFEBから再実行する。
- FEBio dumpによる途中再開は実機検証後の拡張機能とする。

失敗時も、failure class、最終完了stage、証拠、回復可能性、次の許可操作、
人間承認要否をJSONで返す。

## 9. 解析意図を保ったデバッグ

### 9.1 失敗分類

```text
INPUT_OR_REFERENCE_ERROR
GEOMETRY_ERROR
INITIAL_MESH_ERROR
DEFORMATION_MESH_ERROR
MODEL_SEMANTICS_ERROR
NONLINEAR_CONVERGENCE_ERROR
RESULT_EVIDENCE_ERROR
RESOURCE_OR_TOOL_ERROR
```

### 9.2 修正候補

修正候補は次を必須とする。

- 原因仮説と根拠
- 変更対象
- 変更前後の差分
- 影響するCAD面、FE領域、荷重、拘束、接触
- ROIと荷重経路への影響
- 期待する改善
- 想定される副作用
- 検証方法
- 自動実行可否
- ロールバック方法

### 9.3 Negative Jacobianの処理

ハーネスは、初期メッシュ時か変形途中か、発生step/time/iteration、要素ID、
節点、`det(J)`、周辺品質、CAD投影量、ROI/接触/拘束/荷重経路との関係、
最後に収束した状態、接触、残差、変位、エネルギー履歴を証拠として収集する。

局所修正後は、少なくとも次を確認する。

1. 不正要素が解消した。
2. CAD形状差、体積差、面積差、Surface対応が許容内である。
3. ROI、接触面、拘束面、材料境界を意図せず変更していない。
4. 局所剛性と荷重経路への影響が許容内である。
5. 反力、変位、接触状態、エネルギー収支が維持される。
6. 契約で定義した評価量、その発生位置、順位が許容内である。

修正は次に分類する。

- `INTENT_PRESERVING`: 承認済み範囲内で自動再解析可能
- `INTENT_SENSITIVE`: 候補作成まで自動、人間承認後に適用
- `INTENT_CHANGING`: 契約改訂と再承認が必要

材料、荷重、拘束、接触、要素形式、保護形状を変更する修正は原則として
自動採用しない。正常終了だけを修正採用の根拠にしない。

同じ失敗と修正のループを避けるため、失敗指紋、試行済み仮説、結果、
retry budgetを保存する。

## 10. 実装フェーズ

### Phase 1: FEB実行基盤

- 既存FEBの検査と互換性評価
- 解析意図契約と人間承認
- ケース状態機械、attempt、証跡
- FEBio Runner
- LOG/XPLT複合完了ゲート
- 収束エラー分類と修正提案
- 契約で事前承認された数値制御範囲内での診断再実行
- Codex Skill
- 結果検証とレポート

STEPは検査と解析意図作成まで対応する。STEPから完全な物理モデルを生成する
機能はPhase 2とする。Phase 1は既存FEBのメッシュ、材料、荷重、拘束、接触を
自動変更しない。

### Phase 2: STEPからのモデル生成

- STEP形状とトポロジー検査
- 意味付きCADエンティティマップ
- 既存`febio_gmsh_launcher`統合
- Gmshメッシュ生成
- Surface/Domain転写
- 材料、BC、荷重、接触を含むFEB生成
- Tet10 G8 Jacobian、保存量、対応関係の検証

### Phase 3: 解析意図対応デバッグ

- 局所メッシュ修正
- 収束条件の診断反復
- 変更影響ゲート
- ROI、荷重経路、評価量の比較
- リスク別自動修正
- retry budgetと停止判定

## 11. テスト戦略

### 11.1 単体テスト

- JSON Schema
- 状態遷移
- 契約revisionと承認
- パス制限とGit境界
- ハッシュとresume key
- LOG/XPLT判定
- エラー分類
- 変更ポリシー

### 11.2 アダプター契約テスト

小型の合成STEP、FEB、LOG、XPLT fixtureを使い、実製品形状をGitへ入れずに
各アダプターの入出力を検証する。

### 11.3 合成実機E2E

小型モデルで、正常終了、INITのみ、Negative Jacobian、未収束、欠落出力、
キャンセル、タイムアウトを実際のFEBioで再現する。

### 11.4 Codex Skill評価

Codexが次を守ることを代表プロンプトと失敗ケースで検証する。

- 物理条件を勝手に補わない。
- 不足情報を質問する。
- 未承認ケースを正式解析しない。
- CLIの構造化結果に従う。
- 同じ修正を無限反復しない。
- 解析意図に反する修正を承認待ちにする。

### 11.5 Phase 1最終実モデルE2E

Phase 1の最後に、次の実モデルを`02_CAE`で新attemptとして実行する。

```text
Case:
C:\Users\backo\OneDrive\Documents\FEBio\02_CAE\01_Active\
Bottom_Frame\2026-07-30_0729C_local040-screw

Lineage:
codex/local040-rigid-screw-cylinder
3c0e672313962e4d14b4d541d27ae8245488b75d
```

基準仕様は、M2相当の直径2.0 mm、長さ4.0 mmの剛体円筒ネジを実アセンブリの
ネジ軸へ整列し、ABS Tet10本体を変更せず、摩擦なし`sliding-elastic`接触で
2.0 mm押し込む非線形解析である。時間積分は20固定step、`step_size=0.05`とし、
0.1 mmずつ2.0 mmまでの20変位状態を生成する。

現在のケースマニフェストには永久FEBとXPLTが収集されていない。したがって
E2E開始前に、次のpreconditionを満たす。

1. 既存設計履歴が示す権威ある入力FEBを再特定する。
2. 入力FEBのSHA-256、FEBioバージョン、物理条件、ABS Tet10 signatureを記録する。
3. 権威ある外部パスと取得証拠を`01_Input`へ記録し、入力FEBの検証済みコピーを
   既存ファイルへ上書きせず`02_Model`へ正式に取り込む。
4. 既存の基準XPLTとsolver LOGが利用可能なら、同様にハッシュ付き参照証拠として
   取り込む。
5. 権威ある入力を特定できない場合、類似モデルで代用せずE2Eを
   `WAITING_FOR_HUMAN`として停止する。

E2Eはモデル生成を再実施せず、Phase 1の既存FEB入力経路を検証する。新しい
attemptで解析し、少なくとも次を合格条件とする。

1. 解析意図契約を作成し、人間承認を記録できる。
2. 入力FEBの材料、Domain、Surface Pair、BC、rigid constraint、load controller、
   output referenceがすべて解決する。
3. ABS Tet10 node/connectivity signatureが基準から変わらない。
4. 剛体ネジの軸、直径、長さ、接触面、2.0 mm prescribed motionが基準仕様と
   一致する。
5. FEBioがexit code 0、normal terminationを報告する。
6. LOGが`0.05`から`1.00`まで20個の固定収束時刻を示す。
7. Negative Jacobian、missing reference、initial contact penetration failureがない。
8. XPLTを公式FBSで読み、20個の非ゼロtarget stateを確認する。
9. 全剛体節点の変位が各stepの`0.1 * step` mmと許容差内で一致する。
10. 要求された結果fieldが存在し、結果検証と監査レポートを生成できる。
11. 既存ケース成果物のハッシュが変化していない。
12. 実モデル、FEB、XPLT、LOG、レポートをGitまたはGitHubへ入れていない。

このE2Eは、Phase 1ハーネスが実際の非線形接触解析を監査可能に完走できることを
検証する。ABS材料の校正、実ねじ山、摩擦、製造ばらつき、製品強度の妥当性を
証明するものではない。

## 12. 初期リリース完了条件

Phase 1は、既存FEBから解析意図契約を作成し、人間承認後にヘッドレス実行し、
失敗診断、再試行、結果検証、監査可能なレポートまで完走できた時点で機能完成と
する。

リリース受入には、単体、アダプター、合成実機、Codex Skill評価に加えて、
Section 11.5のBottom Frame実モデルE2Eが合格していることを必須とする。

## 13. 非目標

初期Phase 1では次を行わない。

- ChatGPTまたはCodex以外のLLM対応
- MCPサーバー
- STEPからの完全自動物理モデル生成
- 人間承認なしの材料、荷重、拘束、接触、形状変更
- FEBio dumpからの途中再開
- 正常終了だけを根拠とした設計合否
- 実製品CAEデータのGit/GitHub保存
