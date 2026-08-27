# Codex向けFEBio CAE Harness設計 V2

## 1. 文書の位置づけ

- Status: consolidated target design
- Version: 2.0
- Date: 2026-08-27
- Target LLM: Codex only
- Tool repository:
  `C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\febio-tools`
- Canonical remote:
  `https://github.com/A6721jpn/cae-harness.git`
- Real CAE workspace:
  `C:\Users\backo\OneDrive\Documents\FEBio\02_CAE`

本書は、人間が与える完成済みFEB、不完全なFEB、またはSTEP中心の入力と自然言語の
解析目的から、Codexが解析意図を確定し、FEBioモデルの構築、ヘッドレス実行、診断、
解析意図を保った自動修正、結果検証、報告までを行うCAE Harnessの目標仕様である。

本書は次の旧文書を履歴として残したまま、その製品境界と実装方針を置き換える。

- [2026-07-30 初版設計](2026-07-30-febio-llm-cae-harness-design.md)
- [2026-07-30 Phase 1計画](../plans/2026-07-30-febio-llm-cae-harness-phase1.md)
- 2026-08-24以降に試作されたWorkbench、autonomy、model fallback、
  App Server、build/launchの各実装

旧文書と本書が矛盾する場合は本書を優先する。旧Phase 1A--Eは監査履歴であり、
V2へ変換せずに新規実装の手順として再実行してはならない。

## 2. 目的と完成像

利用者は対象モデルと、何を知りたいかを自然言語で与える。Codex CAE Case Agentは
概ね最初の1--3会話turnで、目的、ROI、荷重経路、材料、荷重、拘束、接触、評価量、
許容差を整理する。結果を左右する情報が揃った後は、個々のモデル編集、solver実行、
診断、再試行について人間の承認を繰り返し求めず、解析意図契約の範囲内で自律的に
進める。

完成状態では、少なくとも次を満たす。

1. 完成FEBの小改造と再解析を行える。
2. 不完全FEBから不足条件を特定し、必要な質問後にモデルを完成できる。
3. STEP中心の入力から、明示された物理条件と検証済み参照情報を使ってモデルを
   構築できる。
4. Negative Jacobian、非線形収束、接触、参照不整合などを解析意図に照らして診断し、
   許された範囲では自動修正できる。
5. クライアント画面や会話接続が失われても、開始済みsolverを可能な限り継続し、
   再接続後に証拠と結果を回収できる。
6. 通常経路は完全にヘッドレスであり、FEBio Studio専用機能だけを公式Computer Useの
   fallbackとして利用できる。
7. Codex Desktop AppまたはOrcaの会話履歴、作業領域、thread生存性を製品実行の
   source of truthにしない。
8. 終了コードではなく、モデル、実行、FBS、結果、解析意図の複合証拠で成功を判定する。

## 3. 基本原則

1. **工学判断と安全境界を分離する。** Codexは意味を解釈し、Harnessは状態、権限、
   パス、証拠、予算、昇格条件を決定論的に強制する。
2. **会話を正式状態にしない。** 正式状態はケース内の解析意図revision、入力来歴、
   append-only event、attempt、検証証拠である。
3. **原本を変更しない。** 入力STEP/FEBと既存成果物は不変とし、変更はderived attemptへ
   create-newで保存する。
4. **物理条件を推測で埋めない。** 結果を左右する不足は`ASK_AND_BLOCK`にする。
5. **承認儀式ではなく意図拘束を使う。** 明示指示と質問への回答を解析条件として
   契約化し、契約内の操作は自律化する。
6. **収束を目的化しない。** エラーが消えても、ROI、荷重経路、接触、剛性、評価量を
   損なえば修正失敗である。
7. **合成試験を実機証明と呼ばない。** synthetic adapterは`CONFORMANCE_ONLY`であり、
   production readinessには実adapterと実runtimeの証拠を要求する。
8. **実CAEデータとツールを分離する。** reusable codeは`01_Tools/febio-tools`でGit管理し、
   実モデルと結果は`02_CAE`だけに置く。
9. **製品実行と開発オーケストレーションを分離する。** Orcaは開発専用であり、
   CAE Harnessの実行時依存ではない。

## 4. 製品境界と構成

V2の核はGUIではなく、CLI/APIとして再利用できる自作CAE Harnessである。利用者向け
interfaceはCLI、TUI、既存ホスト、将来の薄いGUIのいずれでもよいが、正式なCAE操作を
直接実行せず、同じgatewayを利用する。

```text
Human
  -> thin conversation/client adapter
       -> isolated Codex CAE Case Agent
            -> durable autonomy supervisor
                 -> febio_cae_harness.workbench gateway
                      -> CAE Harness core
                           -> STEP/FEB/Gmsh/FEBio/FBS adapters
                           -> deterministic validators
                      -> durable solver supervisor
                      -> FEBio Studio fallback adapter (exception only)
```

### 4.1 Codex CAE Case Agent

Case Agentは次を担当する。

- 解析目的と不足情報の理解
- 入力成熟度と参照候補の意味的評価
- 解析意図契約の作成とrevision
- 原因仮説、修正候補、検証計画の選択
- ROI、荷重経路、評価量への影響判断
- Harnessが許可した操作の実行
- blocker、結果、限界の利用者向け説明

デバッグの最終責任は、同じ解析意図契約を保持するCase Agentにある。補助agentを使う
場合も、intent revision、workspace binding、entity map、failure evidence、retry ledgerを
引き渡し、無文脈のsolver fixerとして使わない。

### 4.2 Durable autonomy supervisor

autonomy supervisorは会話clientより長寿命な制御主体であり、次を保持する。

- current case、intent revision、attempt、runのbinding
- solver所有権と進捗監視
- retry budgetとmonotonic guard
- 許可操作と停止条件
- client切断・再接続状態
- completion evidenceの収集責任

Case Agentの提案をそのまま実行せず、Harnessのtyped contractへ変換し、現在のintent、
workspace、attempt、budgetと一致する場合だけformal operationへ進める。

### 4.3 Canonical integration surface

production componentは`febio_cae_harness.workbench`の公開型とschemaだけを共有する。
各streamやadapterが、同名のport、workspace policy、case registry、state、lease、schemaを
再定義してはならない。

唯一のworkspace binding生成経路は次とする。

```python
ValidatedCaseWorkspace.for_case(policy, registry, case_id, case_dir)
```

manifest、event、artifact、App Server turn、solver、Studio fallback、reportは、すべて同じ
`ValidatedCaseWorkspace`とbinding digestを消費する。rawな`case_root`、`cwd`、
`writable_root`をformal operationへ再入力して権限を作り直してはならない。

production compositionは、少なくとも次の実adapterが同じprotocol/schema/API digestと
workspace bindingでhandshakeできた場合だけ`READY`になる。

- `ProductionHarnessAdapter`
- `AutonomyAdapter`
- `ModelSourceAdapter`
- client/Codex transport adapter
- `BuildLaunchAdapter`

欠落adapter、synthetic adapter、signature不一致、runtime identity不明、schema digest不一致は
`BLOCKED`または`CONFORMANCE_ONLY`であり、formal solveを公開しない。

## 5. 入力成熟度とsource authority

### 5.1 対応する入力

| 入力種別 | 判定 | Harnessの初動 |
|---|---|---|
| 完成FEB | `COMPLETE_FEB` | read-only inspection後、原本を複製せずderived attemptで小改造する |
| 不完全FEB | `INCOMPLETE_FEB` | 存在するgeometry/mesh/physicsと欠落条件を項目別に列挙する |
| STEP中心 | `STEP_GEOMETRY` | geometry/topologyを検査し、material/BC/load/contact/ROIは別の意図入力として集める |
| 明示された類似FEB | `EXPLICIT_REFERENCE` | 互換性とlineageを項目別に検査し、採用可能な知識だけを候補化する |

利用者が「完了済み」と説明したFEBも、過去の成功を無条件に継承しない。FEB schema、
reference closure、材料、domain、selection、BC、load、contact、step、output、利用可能なら
過去LOG/XPLT/FBS証拠を検査し、主張と検査結果を区別して記録する。

### 5.2 類似モデルの継承

Harnessはディスク上の類似モデルを勝手に探索して採用しない。利用者が明示的に添付、
指定、またはケースのauthoritative inventoryで許可したものだけを候補にする。

互換性はファイル単位ではなく次の項目単位で判定する。

- 単位系、解析種別、FEBio version/schema
- geometry、mesh、domain、material
- semantic entityとsetの対応
- load、constraint、contact、rigid relation
- step、controller、numerical policy
- ROI、result population、required fields
- 過去結果の検証状態とlineage

各項目を`ADOPTED`、`OVERRIDDEN`、`PROPOSED`、`REFERENCE_ONLY`、`REJECTED`、
`UNRESOLVED`に分類し、source bytes、SHA-256、採用理由、compatibility evidenceを保存する。
STEPとFEBを併用する場合、対象STEPを形状の正本とし、FEBの節点ID、要素ID、面番号を
直接移植せず、semantic entity mapで再対応付けする。

## 6. 解析意図契約と自律運転

### 6.1 解析意図契約

正式なmodel buildまたはsolveより前に、case-scopedな解析意図revisionを作る。最低限、
次を含む。

- 工学的な問いと使用目的
- 対象部品、意味的役割、単位系
- material、load、constraint、contact、analysis step
- ROI、保護形状、荷重経路
- result metric、population、方向、時刻、統計
- 維持すべき不変条件
- 許容するgeometry/mesh/model/numerical変更と上限
- 比較baselineと許容差
- retry、時間、CPU、memory、storage予算
- 停止条件と禁止する結論
- 各条件の出典、仮定、不確かさ

各settingは`USER_SPECIFIED`、`INHERITED`、`OVERRIDDEN`、`INFERRED`、
`UNRESOLVED`、`PROHIBITED`のいずれかを持つ。`INFERRED`は出典、信頼度、検証方法を
必須とし、critical fieldを`INFERRED`だけでformal resultへ昇格してはならない。

### 6.2 意図状態

```text
GATHERING
  -> BOUND
  -> INVALIDATED
  -> ASK_AND_BLOCK
```

- `BOUND`: critical missing fieldがなく、contract hash、source set、workspace、実行policyが
  一体として固定された状態。
- `ASK_AND_BLOCK`: 結果を左右する条件、source authority、unsupported runtime、または
  intent-changing判断の根拠が不足している状態。
- `INVALIDATED`: 新指示、入力変更、source drift、または検証失敗により、現在revisionを
  formal operationへ使用できない状態。

質問への回答は「Agentの修正案を承認する操作」ではなく、新しい解析条件または証拠で
ある。回答から新revisionを作り、再度bindingを評価する。通常の実行・修正ごとに
`APPROVE`、nonce、承認画面を要求しない。

### 6.3 自律化の境界

intentが`BOUND`で、操作が次をすべて満たす場合、Case Agentは追加承認なしで実行できる。

1. workspace、case、intent revision、attemptが一致する。
2. 変更種類と大きさがcontractのautonomy envelope内である。
3. ROI、荷重経路、保護形状、material/BC/load/contact不変条件を破らない。
4. retry budget、resource budget、monotonic guardを満たす。
5. before/after差分と必要な検証を生成できる。
6. 失敗時に原本へ影響せず新attemptへ戻れる。

次は必ず`ASK_AND_BLOCK`にする。

- loadの方向、位置、量、履歴、単位が未確定
- constraintの対象、自由度、座標系、rigid relationが未確定
- contact pair、接触則、摩擦、初期gap/overclosureが未確定
- material family、parameter、温度・速度依存、calibration sourceが未確定
- ROI、metric、許容値、抽出方法が未確定
- 類似FEB/STEPの互換性またはlineageが未確認
- 修正がcontractの物理、不変条件、保護形状を変更する
- production runtime、workspace、lease、evidence authorityを検証できない

## 7. ケース状態とデータ

### 7.1 Lifecycle

```text
CASE_CREATED
  -> INPUT_INSPECTED
  -> INTENT_GATHERING
       -> ASK_AND_BLOCK --(answer/evidence)--> INTENT_GATHERING
       -> INTENT_BOUND
  -> MODEL_BUILDING
  -> PREFLIGHT
  -> SOLVING
  -> VERIFYING
  -> REPORTING
  -> COMPLETE

MODEL_BUILDING/PREFLIGHT/SOLVING/VERIFYING
  -> DIAGNOSING
  -> MODEL_BUILDING/PREFLIGHT/SOLVING

any active state -> FAILED | CANCELLED
```

commandが成功したこととcaseが`COMPLETE`であることを別に表現する。
`SOLVED`、`REPORTED`、backendの`success`だけを利用者向け`SUCCESS`へ投影しない。

### 7.2 Control plane

client接続状態は解析lifecycleと直交する。

- `HEADLESS`: 通常の自律・solver経路。
- `FALLBACK_ACTIVE`: FEBio Studio専用操作だけを実行中。
- `DISCONNECTED`: client/windowが失われた状態。開始済みsolverの監視は継続するが、
  新しいmodel mutation、retry、Studio操作は開始しない。
- `RECONNECTING`: case、intent、attempt、supervisor、event chainを再検証中。

### 7.3 ケース配置

```text
02_CAE/<case>/
  README.md
  CASE_MANIFEST.json
  01_Input/
  02_Model/
  03_Result/
  04_Report/
  05_Verification/
  90_Temporary/
    attempts/<attempt-id>/
```

real modelに対する作業書込みは、検証済みcase配下の`90_Temporary`またはvalidated
attempt rootだけに許可する。検証済みの最終成果物だけを永久領域へcreate-newで昇格する。
実STEP、FEB、FSM、LOG、XPLT、MSH、INP、VTU、VOL、reportをtool repositoryへ
入れない。

### 7.4 Event、manifest、registry

- authoritative eventはappend-only hash chainとする。
- `CASE_MANIFEST.json`は現在状態のatomic projectionであり、単独ではauthorityにしない。
- case registryはplain JSONの書換可能な内容を信用せず、case binding、immutable origin、
  manifest/event head、generationを照合する。
- production leaseとregistry recordは、全identity fieldを含むdigestと認証情報を持つ。
- secret、auth token、raw owner token、Desktop state pathをevent payloadへ保存しない。
- event gap、duplicate、wrong previous hash、wrong case、replay、tamperはfail-closedにする。

## 8. Solver所有権と切断耐性

solverは会話clientまたは任意のGUI processの子として所有しない。durable supervisorが
attemptごとに起動し、少なくとも次を記録する。

- case/workspace binding digest
- intent revisionとcontract digest
- attempt ID、run ID、generation
- supervisor instance identity
- solver executable path/hash/version
- PID、process creation time、process tree identity
- argv、cwd、許可環境
- start/end、progress、exit reason
- LOG/XPLT/dumpのfreshness、bytes、hash

production `ActiveAttemptLease`はsupervisorだけが発行できる。public synthetic constructorは
test専用とし、formal operationでは拒否する。leaseは上記全identity、current-attempt registry、
generation、event headへ暗号学的にbindingし、再利用、PID reuse、別case、別workspace、
別supervisor、古いgenerationを拒否する。

client/windowの終了、crash、transport切断時は次の順で処理する。

1. すでにsupervisor-ownedで開始済みのsolverと証拠収集を継続する。
2. client不在中は新しいmodel mutation、retry、result promotionを開始しない。
3. supervisorは進捗、終了、artifact hashをeventへ記録する。
4. 再接続時にcase registry、event head、lease、OS process identityを照合する。
5. 一致すれば監視・検証を再開し、不一致なら結果を隔離して`ASK_AND_BLOCK`または
   `FAILED`とする。

cancelはcase-owned control commandだけから行い、PIDの直接killを正式操作にしない。
対象attemptの所有権を再確認してから、そのprocess treeだけを停止する。

## 9. モデル構築とメッシュ検証

### 9.1 完成FEB

原本のmaterial/domain/reference/load/BC/contact/step/output signatureを固定し、要求された
小改造だけをderived FEBへ適用する。変更対象外signatureと原本hashが不変であることを
preflightで検証する。

### 9.2 不完全FEB

存在する設定と欠落設定を混同しない。reference closure、empty set、unbound domain、
missing material/load/BC/contact/outputを列挙し、critical missingは質問する。回答後に
semantic entity mapとintent revisionを更新し、新attemptでモデルを生成する。

### 9.3 STEP中心入力

STEPからgeometry、body、topology、size、adjacency、必要ならnamed featureを検査する。
材料、荷重、拘束、接触をgeometryから推測しない。明示された条件と、互換性が確認された
reference knowledgeから、domain、set、contact pair、mesh policy、FEBを構築する。

Tet10を使う場合、単にinvalid element countが0であることでは不十分である。少なくとも
全要素のG8 integration point Jacobian、最小値と位置、orientation、surface/domain対応、
geometry deviation、保存量を検査する。

## 10. 解析意図を保った自動デバッグ

### 10.1 責任と診断単位

失敗を次のように分類する。

```text
INPUT_OR_REFERENCE_ERROR
GEOMETRY_ERROR
INITIAL_MESH_ERROR
DEFORMATION_MESH_ERROR
MODEL_SEMANTICS_ERROR
CONTACT_ERROR
NONLINEAR_CONVERGENCE_ERROR
RESULT_EVIDENCE_ERROR
RESOURCE_OR_TOOL_ERROR
```

各診断は、原因仮説、根拠、変更対象、before/after、影響entity、ROI/load-path/metricへの
影響、期待改善、副作用、検証方法、rollback、retry costを持つ。

### 10.2 Impact分類

| 分類 | 動作 |
|---|---|
| `INTENT_PRESERVING` | autonomy envelope内なら自動適用し、before/after検証を行う |
| `INTENT_SENSITIVE` | 感度差分がcontract tolerance内と証明できる場合だけ自動適用する |
| `INTENT_CHANGING` | 自動適用せず、必要な物理条件を質問して`ASK_AND_BLOCK`にする |

材料、荷重、拘束、接触則、要素形式、保護形状の変更は、contractに明示的な許容範囲が
ない限り`INTENT_CHANGING`である。

### 10.3 Negative Jacobian

初期メッシュか変形途中かを区別し、step、time、iteration、element/node、全G8 `det(J)`、
周辺品質、CAD投影量、ROI/接触/拘束/荷重経路との関係、最後に収束した状態、残差、変位、
反力、接触、energy履歴を収集する。

局所修正後は最低限、次を比較する。

1. 不正要素と近傍品質が改善した。
2. geometry deviation、体積、面積、surface/domain対応が許容内である。
3. ROI、接触面、拘束面、材料境界、保護形状が維持された。
4. 局所剛性と荷重経路への影響が許容内である。
5. 反力、変位、接触状態、energy収支がbaselineと整合する。
6. 評価量、発生位置、順位、populationがcontract tolerance内である。

solverが通ったことだけを修正採用の根拠にしない。

### 10.4 収束エラーとretry

時間刻み、line search、quasi-Newton、接触制御などの数値変更は、物理モデルを変えない
範囲と上限をcontractへ定義できる。各retryは新attemptとし、失敗fingerprint、仮説、変更、
結果、消費budgetをledgerへ追加する。

次の場合は自動反復を停止する。

- 同じfailure fingerprintと実質同じ変更を繰り返す
- quality、residual、energy、progressが単調に改善しない
- retry/resource budgetを超える
- intent impactを証明できない
- 新しい物理条件の選択が必要になる

## 11. FEBioヘッドレス実行と成功条件

通常経路はHarnessが所有する`febio4.exe`のヘッドレス実行と公式FBS readerである。
次をすべて満たした場合だけ、解析lifecycleを`COMPLETE`、利用者向け状態を`SUCCESS`にする。

1. production leaseで所有されたprocess treeが正常終了した。
2. LOGとXPLTが今回attemptでfreshに生成された。
3. LOGがnormal termination、期待step、最終timeを示す。
4. fatal、missing reference、Negative Jacobian、未解決warningがない。
5. XPLTをofficial FBS runtimeで読み取れる。
6. expected state/timeとrequired fieldsが存在し、有限かつ対象associationが明確である。
7. model/reference closure、mesh/G8、geometry、kinematicsが検証済みである。
8. ROI、result population、metric、差分基準がintentと一致する。
9. completion decisionと全evidence digestがcase、attempt、intentへbindingされている。
10. 検証済みreportがcreate-newで昇格されている。

INIT-only XPLT、stale file、読めないFBS、missing field、association ambiguity、hash drift、
synthetic adapterの成功はproduction successではない。値を取得できない項目は推測せず
`UNAVAILABLE`とする。

## 12. FEBio Studio / Computer Use fallback

Computer Useはメインpipelineではない。headless adapterでは実行できず、かつ解析に必要な
FEBio Studio機能が明確な場合だけ利用する。

fallback開始には次を要求する。

- 必要なStudio専用操作と、headless不能である根拠
- supportedな公式Computer Use経路
- runtimeの供給元、署名、version、hash
- 同じ`ValidatedCaseWorkspace`とattempt binding
- 許可する入力、操作、出力path
- 操作前snapshotと期待する差分

Studioはadapter経由で起動し、操作event、画面証拠、保存artifact、before/after diffを記録する。
生成物をそのままformal modelにせず、ModelSourceAdapterが再検査し、derived attemptへ採用した
後にheadless pipelineへ戻す。公式経路または証拠化を確認できなければ、対応済みと表示せず
`ASK_AND_BLOCK`または`FAILED`にする。

## 13. Codex、Desktop、Orcaからの分離

製品はCodex subscriptionを利用できるが、Codex Desktop Appのthread、memory、SQLite、
workspace、config、credential、skill、画面を読み込みまたはimportしない。CAE用のCodex home、
thread registry、case mapping、logを独立させ、1 caseを1専用threadへbindingする。

初期production adapterは、versionとhashを固定した公式Codex CLIのsupported interfaceを使う。
subscription loginを要求し、API key課金へ暗黙にfallbackしない。公式App Serverを使う場合も、
公開されたprotocol/schemaからclientを生成または検証し、Desktop私有protocolやhelper executableの
直接呼出しへ依存しない。利用するCodex executable、authentication mode、protocol/schema、
capability setを起動時handshakeと実行証拠へbindingする。

同じWindows user内の論理分離であり、別OS accountやVMと同等のsecurity boundaryとは
主張しない。Desktop側とHarness側に異なるnegative canaryを置き、thread list、effective
config、protocol traffic、turn response、保存state、process environmentに相互のcanaryが
現れないことを実機検証する。

Orcaは並列開発、worktree、terminal、agent間連絡、reviewに利用できる。ただし製品runtime、
case state、solver ownership、解析証拠、利用者interfaceの必須依存にしない。

## 14. Build、GitHub、起動契約

tool source、test、synthetic fixture、Skill、docsはcanonical GitHub repositoryでversion管理する。
実CAE artifactはrepository boundary scanでcommit、package、releaseから拒否する。

通常の開発版起動は次に固定する。

1. 外部build scriptがclean committed sourceを検証する。
2. build、unit/contract test、package、installed-only smokeを実行する。
3. source commit、build time、package hash、test summary、launch originを
   `launch-manifest.json`へ記録する。
4. sibling staging folderへ完全な成果物を作る。
5. 全gate成功時だけ
   `%LOCALAPPDATA%\FEBioCaeWorkbench\latest-development`へatomic stageする。
6. Start Menu shortcutは常にこの固定development launch folderのlauncherを指す。
7. build失敗時はcanonical folderを変更せず、旧版を自動起動しない。
8. 起動中のnetwork update、remote candidate探索、`current.json`、version別LKG選択は
   実装しない。

shortcutの存在だけで合格にせず、target、arguments、working directory、iconを再読し、
起動後に表示または`status --version`で得たcommit/build identityがlaunch manifestと
一致することを確認する。

## 15. セキュリティとfail-closed要件

少なくとも次のnegative testをproduction gateに含める。

- synthetic leaseをformal retryへ渡す
- lease fieldの一部を改ざんする
- wrong case、wrong workspace、wrong attempt、wrong intentを混ぜる
- event/manifestのgap、duplicate、reorder、replay、tamper
- case registry JSONを手編集する
- PID reuse、process creation time不一致、別executableへの差替え
- Case Aのartifact/turnへCase Bのpathを渡す
- relative path、traversal、junction、symlink、reparse point、ADS
- Desktop home、tool root、`02_CAE` rootそのものへのwrite
- GUI/client切断中のformal mutationまたはretry
- synthetic adapterをproduction `READY`としてcomposeする
- FBS、required field、final time、mesh/G8、ROI evidenceのいずれかを欠かす
- unknown request、partial protocol message、reconnect時の古いgeneration

これらのどれかを受理する実装は、通常系testがgreenでもproduction readyではない。

## 16. 検証レベルとE2E

検証結果は次のレベルを混同しない。

| Level | 意味 |
|---|---|
| `UNIT` | pure function、schema、policy単体 |
| `CONFORMANCE_ONLY` | synthetic port、fixture、shape、round-trip |
| `INTEGRATED` | 全production adapterを同じbindingで結線 |
| `INSTALLED` | clean packageをsource checkoutなしで実行 |
| `REAL_RUNTIME` | 実FEBio、official FBS、必要ならofficial Computer Use |
| `REAL_CASE` | `02_CAE`の実モデルとauthoritative intent/evidence |

必須acceptance matrixは次とする。

| ID | 経路 | 合格条件 |
|---|---|---|
| E2E-01 | 完成FEBの小改造 | 原本不変、derived attempt、impact、solve、verify、reportがformal gateway経由 |
| E2E-02 | 不完全FEB | missing physicsを質問し、回答後だけintent/modelを更新。推測補完なし |
| E2E-03 | STEP中心 | geometry inspectionからmodel/mesh/solve/reportへ進み、条件の出典と限界を保存 |
| E2E-04 | Negative Jacobian | 発生位置を特定し、意図保全修正とROI/load-path/metric比較を証拠化 |
| E2E-05 | Studio fallback | GUI専用操作だけを公式経路で行い、差分を再検査してheadlessへ復帰 |
| E2E-06 | client crash | client終了後も開始済みsolverが継続し、切断中の新規mutationを拒否して再接続 |
| E2E-07 | authority attacks | Section 15のnegative matrixをすべて拒否 |
| E2E-08 | external build/launch | clean commitからatomic stageし、shortcutと実行identityが一致 |
| E2E-09 | regression | case/event/intent/runner/FBS/report/boundaryの既存testを保持 |
| E2E-10 | BottomFrame | 最後に剛体ネジ非線形実モデルを実FEBio/FBSで完走し、全証拠を確認 |

E2E-01--09が完了する前にE2E-10を最終合格として実行しない。実機runtimeがない、
権威ある入力が不明、テストが中断、環境ACLでcollection不能などの場合は、コード失敗または
成功と断定せず、未検証gateとして報告する。

### 16.1 BottomFrame最終E2E

対象caseは次である。

```text
C:\Users\backo\OneDrive\Documents\FEBio\02_CAE\01_Active\
Bottom_Frame\2026-07-30_0729C_local040-screw
```

権威ある入力FEB、source hash、FEBio version、intent revision、ABS Tet10 signature、rigid screw
axis/diameter/length、contact、2.0 mm prescribed motion、20固定stepを再確認する。既存artifactを
上書きせずfresh attemptを実行し、normal termination、0.05--1.00の20収束時刻、official FBS、
required fields、剛体変位、mesh/G8、ROI/result validation、report、preexisting inventory不変を
確認する。

このE2EはHarnessの監査可能な非線形接触workflowを証明するものであり、ABS材料校正、
実ねじ山、摩擦、製造ばらつき、製品強度の妥当性を証明するものではない。

## 17. 段階的な完成条件

V2 targetは次の順で到達する。

1. **Foundation:** canonical contract、workspace、event、lease、registry、production adapter。
2. **Complete FEB headless:** 完成FEBのinspect、small change、solve、FBS、report。
3. **Autonomy and incomplete FEB:** intent gathering、`ASK_AND_BLOCK`、自律診断・retry。
4. **STEP model build:** geometry、semantic map、mesh、physics-bound FEB generation。
5. **Fallback and resilience:** Studio fallback、client crash、solver continuation、reconnect。
6. **Qualification:** installed/system/security matrixとBottomFrame最終E2E。

各段階は前段のproduction evidenceを再利用するが、合成fixtureだけで後段を完成扱いにしない。

## 18. 非目標

- Codex以外のLLM対応
- OrcaをCAE Harnessの製品runtimeまたは利用者UIとして組み込むこと
- 初期実用版に専用3D viewerや高機能GUIを作ること
- 未指定の類似モデルを自動探索して物理条件を継承すること
- 根拠なしのmaterial、load、constraint、contact、ROIの自動決定
- 起動時updateまたはネットワーク依存のversion選択
- 正常終了だけを根拠にした解析成功または設計合否
- synthetic testを実FEBio、Studio、crash recovery、実モデルE2Eの代替にすること
- 実製品CAEデータをGitまたはGitHubへ保存すること

## 19. 設計完了の判定

V2実装を「完成」と表現できるのは、Section 16の全必須E2Eがfresh evidence付きで合格し、
tool repositoryがclean、launch identityがsource commitと一致し、real CAE artifactがGitへ
入っておらず、BottomFrame最終E2Eが合格した場合だけである。

未実施、環境blocked、syntheticのみ、partial test、未回収solver結果、未検証Studio経路を
完成件数へ含めない。報告は、確認済み事実、推測、未確認事項を明確に分ける。
