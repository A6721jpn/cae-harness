# FEBio CAE Harness V2 復旧・実装計画

## 1. 文書の位置づけ

- Status: executable recovery and delivery plan
- Date: 2026-08-27
- Governing design:
  [Codex向けFEBio CAE Harness設計 V2](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md)
- Canonical repository:
  `C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\febio-tools`
- Canonical remote:
  `https://github.com/A6721jpn/cae-harness.git`

本計画は、既存のPhase 1実装と複数のLuna試作worktreeから利用可能な資産を救出し、
V2の一つのcanonical implementationとして再構成するための実行契約である。既存branchを
一括mergeして「統合済み」とはしない。契約、authority、test evidenceを確認した小さな
commitだけを採用する。

## 2. 開始条件と禁止事項

開始時に次を記録する。

- canonical `origin` URL、`origin/master` SHA、local base SHA
- canonical checkoutと全Orca worktreeのbranch、HEAD、dirty/staged/untracked状態
- 各candidate branchのcommit graphとmasterとの差分
- 実行中のFEBio/FBS/Codex/worker process
- 利用可能なPython、FEBio、FBS、Codex、Computer Use identity

以下は禁止する。

- dirty worktreeの内容を一括copyまたは一括mergeする
- 数百fileのstaged snapshotを一つのcommitとして採用する
- test未実行のworker出力を`done`として統合する
- synthetic adapterをproduction adapterとして結線する
- canonical contractを各worker branchで複製する
- 実CAE artifactをtool repositoryへ移動する
- 既存CAE fileを削除または上書きする
- V1のapproval ceremony、startup updater、`current.json`/LKGを再導入する

本計画の範囲内では、日常的な実装、commit、feature branchへのpush、test、review、
worktree整理について利用者の逐次承認を求めない。実ケースの意味を変える物理条件が
証拠から確定できない場合だけ`ASK_AND_BLOCK`とする。

## 3. 復旧方針

### 3.1 新しい統合線

`origin/master`の確認済みSHAからV2統合branchを作る。既存のsupervisor branch、combined
worktree、Luna branchをそのまま統合branchにしない。

候補commitの採用条件は次のとおり。

1. scopeが一つの責務に限定されている。
2. real CAE data、credential、Desktop stateを含まない。
3. V2 canonical type/schemaを再定義しない。
4. diff reviewと対象testを再現できる。
5. known limitationと未検証事項がhandoffに明記されている。
6. commit後のcandidate worktreeがcleanである。

条件を満たさない大規模commitは、直接cherry-pickせず、必要な振る舞いを小さなRED testと
最小実装としてV2 branch上で再構成する。

### 3.2 既存試作の扱い

各既存branchは次の四分類にする。

| 分類 | 扱い |
|---|---|
| `REUSE_COMMIT` | scopeとtestが明確なcommitをcherry-pick候補にする |
| `PORT_BEHAVIOR` | codeは採らず、testまたは要求される振る舞いだけを移植する |
| `REFERENCE_ONLY` | design、失敗記録、review findingだけを参照する |
| `REJECT` | V2と矛盾、authority bypass、重複contract、証拠不足のため不採用 |

少なくともintegration、autonomy、model/fallback、App Server/UI、build/launch、Phase 1の
各系統を個別に分類する。分類結果にはbranch、commit、files、test、security finding、
採否理由を含める。

## 4. 実装DAG

```text
P0 Baseline and recovery inventory
  -> P1 Canonical contracts and authority hardening
       -> P2 Complete-FEB headless vertical slice
       -> P3 Intent/autonomy and incomplete-FEB path
       -> P4 Build/launch pipeline
            P2 + P3 -> P5 STEP/model-build path
            P1 + P2 -> P6 Studio fallback and disconnect resilience
       P2 + P3 + P4 + P5 + P6 -> P7 Integrated qualification
            -> P8 BottomFrame final E2E and delivery
```

P2、P3、P4はP1のcanonical contractが固定された後、別worktreeで並列化できる。P5とP6は
依存する実adapterのhandshakeが確定してから開始する。

## 5. Work package

### P0 — Baselineと救出inventory

成果物:

- `recovery-inventory.json`
- `candidate-commit-review.md`
- canonical base SHAとbranch/worktree map
- dirty/staged/untrackedの保全記録
- V2とV1のsupersession確認

Gate:

- canonical checkoutがclean
- real CAE bytesがtool repoに0件
- candidateを変更せずread-only監査できた
- active solverを開発整理で停止していない

### P1 — Canonical contractとauthority hardening

一人のintegration ownerが`febio_cae_harness.workbench`の公開surfaceを管理する。他workerは
schema/port変更を直接forkせず、必要変更をintegration ownerへ提案する。

実装対象:

- `ValidatedCaseWorkspace`とdurable `CaseWorkspaceRegistry`
- protocol/schema/API digestとexact handshake
- production/synthetic adapterの区別
- append-only event/manifest projection
- authenticated production attempt lease
- current-attempt generation、PID reuse、replay/tamper防止
- command resultとlifecycle statusの分離
- `ASK_AND_BLOCK` clarification model

RED canary:

- forged synthetic leaseからformal retry
- edited registry JSONから別case adoption
- wrong case/workspace/intent/attempt混在
- event gap/reorder/replay
- symlink/junction/ADS/traversal
- missing adapterを`READY`表示

Gate:

- negative matrixがすべてfail-closed
- synthetic compositionは`CONFORMANCE_ONLY`
- real adapterがない構成は`BLOCKED`
- public contractの重複定義が0件

### P2 — 完成FEBのheadless vertical slice

実装対象:

- source resolutionとimmutable input adoption
- FEB read-only inspectionとreference closure
- derived attemptによるsmall modification
- model/preflight evidence
- durable supervisorによるFEBio execution
- LOGとofficial FBSのcompletion gate
- result validationとreport promotion
- cancelとrestart/reopen

Gate:

- synthetic Tet4のsuccess/failure matrix
- INIT-only、stale XPLT、missing field、Negative Jacobian、timeout、cancelを正しく拒否
- clean installed packageからsource checkoutなしで完走
- original FEB hash不変

### P3 — Intent/autonomyと不完全FEB

実装対象:

- maturity classification
- rich intent revisionとautonomy envelope
- critical missing fieldのgrouped clarification
- legacy waiting状態から`ASK_AND_BLOCK`へのclosed projection
- failure fingerprint、retry ledger、monotonic guard
- `INTENT_PRESERVING`/`SENSITIVE`/`CHANGING`判定
- 不完全FEBのmissing physics補完経路

Gate:

- 物理条件を推測せず質問する
- 回答を修正承認として扱わず、新intent revisionへbindingする
- contract内retryは追加承認なしで進む
- 同一failure loop、budget超過、intent-changing変更を停止する
- ROI/load-path/metric evidenceなしの修正採用を拒否する

### P4 — GitHub、build、launch

実装対象:

- tool-only repository boundary scan
- clean commitからのbuild/test/package/installed-only smoke
- `launch-manifest.json`
- sibling stagingと`latest-development`へのatomic stage
- Start Menu shortcutのinstall/repair/inspection
- 実行identity表示またはCLI status

Gate:

- shortcutがversion別folderを指さない
- 起動時network/update codeが存在しない
- `current.json`/LKG selectionが存在しない
- build失敗時にcanonical folderがbyte単位で不変
- shortcut起動後のcommit/build identityがmanifestと一致

### P5 — STEP/model-build

実装対象:

- STEP geometry/topology inspection
- semantic entity map
- explicit reference compatibility
- Gmsh adapterとmesh generation
- domain/set/surface/contact mapping
- physics-bound FEB compiler
- Tet10全要素G8 Jacobian、geometry deviation、保存量検証

Gate:

- geometryからmaterial/load/BC/contactを推測しない
- incompatible referenceをadoptしない
- missing physicsは`ASK_AND_BLOCK`
- STEPからderived FEB、solve、FBS、reportまでのsynthetic/controlled E2E

### P6 — Studio fallbackと切断耐性

実装対象:

- headless unsupported判定
- official Computer Use runtime preflight
- FBS/FEBio Studioのadapter起動
- allowed operation/output、screen evidence、before/after diff
- Studio artifactの再検査とheadless復帰
- client disconnect後のsolver継続
- reconnect時のlease/event/process照合

Gate:

- unsupportedなComputer Use経路を対応済みと表示しない
- Studioを通常pipelineに使わない
- client終了後もsupervisor-owned solverが継続する
- 切断中の新規mutation/retry/promotionを拒否する
- 再接続後にfresh resultとevidenceを回収する

### P7 — Integrated qualification

一時combined worktreeではなく、cleanなV2 integration branchのcommitからqualificationする。

順序:

1. unit/schema/policy
2. contract/conformance negative matrix
3. production adapter integration
4. full harness regression
5. clean wheel/package install
6. installed synthetic E2E
7. real FEBio/FBS E2E
8. official Studio fallback
9. client crash/reconnect

環境ACL、missing runtime、認証、test collection failureは実装のpass/failへ混ぜず、
`ENVIRONMENT_BLOCKED`としてexact command、exit code、stderr、未実施範囲を保存する。

### P8 — BottomFrame最終E2Eとdelivery

P7の全必須gateがgreenになった後だけ実行する。

対象:

```text
C:\Users\backo\OneDrive\Documents\FEBio\02_CAE\01_Active\
Bottom_Frame\2026-07-30_0729C_local040-screw
```

手順:

1. preexisting inventoryを初回write前に固定する。
2. authoritative FEB、source hash、intent、FEBio/FBS runtimeを再確認する。
3. ABS Tet10 signature、rigid screw、contact、2.0 mm motion、20固定stepを検査する。
4. 原本を変更せずfresh attemptでsolveする。
5. LOG、official FBS、final time、required fields、G8、rigid displacement、ROIを検証する。
6. reportを生成し、全artifactをcase/attempt/intentへhash bindingする。
7. preexisting inventory不変とrepository boundaryを再監査する。

未特定の入力を類似モデルで代用しない。権威ある物理条件が不足する場合だけ
`ASK_AND_BLOCK`とする。実行時間が長いことを停滞または失敗と誤判定せず、supervisorの
progress evidenceで監視する。

## 6. Orcaと並列agentの運用

Orcaは開発専用のdurable coordination layerとして使用する。Codex Desktop task間の会話を
唯一の通信経路にしない。

各worker taskには次を固定する。

- task ID、owner、base commit
- worktree pathと許可file scope
- canonical contract version/digest
- expected RED/GREEN command
- forbidden changes
- output commitとhandoff schema
- timeoutではなく、完了・質問・blockerのevent条件

workerは`worker_done`を送る前にfocused test、diff review、commit、clean statusを完了する。
handoffにはcommit SHA、changed files、test command/exit code、known limitations、conflict riskを
含める。未コミット差分をhandoff成果物にしない。

監督agentは常時会話を読み続けず、`worker_done`、`escalation`、test failure、no-progress
thresholdをevent-drivenに処理する。定期heartbeatは状態報告専用とし、同じ停止状態が3回または
3時間継続した場合だけ詳細警告を出す。監督tokenを、終了したworkerや変化のないterminalの
反復読取りへ消費しない。

並列化は最大限利用するが、同じcanonical contract fileを複数workerへ同時割当しない。
Luna workerは小さなbounded implementationを担当し、独立したSol reviewerがsecurity、
authority、cross-stream contract、test evidenceを確認する。

## 7. Commit、integration、push規約

1. 1 commitは1つの検証可能な責務に限定する。
2. generated cache、test output、real CAE dataをstageしない。
3. `MM` fileはstagedとunstagedの両diffを確認する。
4. focused test後、関連contract test、最後にintegration regressionを行う。
5. worker branchを丸ごとmergeせず、review済みcommitだけを順に統合する。
6. conflict解消後は当該testを再実行する。
7. integration branchがcleanかつgate greenになったcheckpointをGitHubへpushする。
8. push済みという事実をtest成功またはrelease完成の代替にしない。

abandoned worktreeは、candidate inventory、commit回収、dirty差分の保全判定が完了した後に
Orca管理機能で削除する。worktree directoryを手動の再帰削除で整理しない。

## 8. Test evidence契約

各test runは最低限、次を保存する。

- source commit、dirty status、runtime identity
- exact command、cwd、start/end、exit code
- collected/passed/failed/skipped count
- stdout/stderrまたはlog path/hash
- synthetic/installed/real-runtime/real-caseのlevel
- environment blockerと未実施scope

途中で中断されたrun、collection error、権限エラー、依存欠落、出力未回収をgreen countへ
含めない。一部suiteだけの成功を全面greenと表現しない。

## 9. Completion gate

次をすべて満たした場合だけV2 deliveryを完成扱いにする。

1. V2 canonical contractと全production adapterが同じhandshakeで`READY`。
2. authority negative matrixが全てfail-closed。
3. complete FEB、不完全FEB、STEPの各経路がacceptanceを満たす。
4. intent-preserving debugとNegative Jacobian検証が成立する。
5. client crash中のsolver継続とreconnectが実機で成立する。
6. official Studio fallbackが実機で成立する。利用環境で非対応の場合はV2 delivery未完成とし、
   headless-only milestoneとしてだけ区別して報告する。
7. clean build、installed-only smoke、fixed shortcut launch identityが一致する。
8. BottomFrame最終E2Eがfresh evidence付きで合格する。
9. real CAE artifactがGit/GitHubに0件。
10. integration branchがcleanで、GitHub上のcommitとdelivery evidenceが一致する。

完了報告は、実装済み、検証済み、未検証、unsupported、environment blockedを分け、
各claimにcommit、test、artifactまたはcase evidenceを対応付ける。
