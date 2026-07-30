# FEBio LLM CAE Harness Phase 1 Program Plan and Acceptance Contract

> **For agentic workers:** This file is the program index and acceptance
> contract. Execute the linked Phase 1A–E plans in order. Do not implement from
> an older task body or reconstruct missing steps from this summary.

**Goal:** Codexが、人間承認済みの解析意図を保持したまま、既存FEBの検査、
FEBio 4.12のヘッドレス実行、LOGと公式FBSによる複合判定、失敗診断、
承認範囲内の診断再実行、結果検証、監査レポートまでを安全に完走できる
Phase 1 CAE Harnessを構築する。

**Architecture:** Codexは解析意図の理解、質問、診断仮説、変更影響の説明を
担当する。独立Pythonパッケージ`apps/febio_cae_harness`は、状態遷移、
承認、パス、hash、attempt、プロセス所有、証跡、昇格を決定論的に強制する。
Codex Skillは両者を接続するが、安全境界はCLIにも実装する。

**Approved design:**
[Codex向けFEBio CAE Harness設計](../specs/2026-07-30-febio-llm-cae-harness-design.md)

## 1. Executable plan suite

次の5文書を同一のplan-suite commitに含め、順番どおり実行する。

1. [Phase 1A: Core Contracts and Case State](2026-07-30-febio-cae-harness-phase1a-core.md)
2. [Phase 1B: Runner and Evidence](2026-07-30-febio-cae-harness-phase1b-runner-evidence.md)
3. [Phase 1C: Orchestration and Report](2026-07-30-febio-cae-harness-phase1c-orchestration-report.md)
4. [Phase 1D: Codex Skill, Packaging, and Release](2026-07-30-febio-cae-harness-phase1d-codex-release.md)
5. [Phase 1E: BottomFrame Real-model E2E](2026-07-30-febio-cae-harness-phase1e-real-model-e2e.md)

各サブプランが実装手順の正本である。各Taskには、対象ファイル、公開interface、
実際の失敗test、REDコマンドと期待failure、最小実装、GREENコマンド、exact
`git add`、Task単位commitを含める。本書とサブプランが矛盾する場合は作業を
止め、より安全な制約を維持してplan suite自体をreview・commitし直す。

依存関係は次のとおり。

```text
Phase 1A core
  -> Phase 1B runner/evidence
       -> Phase 1C orchestration/report
            -> Phase 1D Codex Skill/release
                 -> Phase 1E BottomFrame real E2E
```

Phase 1Eが合格するまでPhase 1 releaseを完成扱いにしない。

## 2. Workspace and repository boundary

- Tool repository:
  `C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\febio-tools`
- Implementation branch: `codex/febio-cae-harness-phase1`
- Isolated worktree:
  `C:\Users\backo\OneDrive\Documents\FEBio\01_Tools\_worktrees\febio-cae-harness-phase1`
- Real CAE root:
  `C:\Users\backo\OneDrive\Documents\FEBio\02_CAE`
- Bootstrap Python 3.12:
  `C:\Users\backo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`

Reusable code、合成fixture、test、Skill、package、文書だけをtool repositoryへ
置く。実製品STEP/FEB/LOG/XPLT/FBS結果/レポートは`02_CAE`だけへ置き、Gitまたは
GitHubへ追加しない。既存`febio_gmsh_launcher`は変更せずregressionだけを行う。
remoteの追加・変更・pushは別途明示承認がない限り行わない。

実装開始時はPhase 1A Setup Gate 0を使い、次をfail-closedで確認する。

- 本書とPhase 1A–Eが同一commitに存在する。
- そのcommitの6 blobについてpath、bytes、SHA-256、git blob IDを
  `2026-07-30-febio-cae-harness-phase1-approved-suite.json`へcreate-newで記録し、
  Setup Gate commitに含める。
- plan commit、approved-suite commit、hash-only baseline、clear-text review
  previewの各SHA-256へbindした人間応答を機械照合し、approved-suite commitの
  direct childへhash-only boundary-review manifestだけをcommitする。
- canonical repositoryと全worktreeのdirty inventoryを記録した。
- target branch/worktreeが衝突しない。
- nested `AGENTS.md`をworktree内で再読した。
- launcher baseline testがgreenである。

Phase 1E計画に含まれるBottomFrame固有のpath、identifier、artifact hashは、
plan-suite commitに既に存在する「承認済み履歴参照」として扱う。Setup Gate 0で
各content参照をidentifier hash、repository-path SHA-256、1-based ordinal、
raw-line SHA-256へ、既存identifier-bearing pathをidentifier hashとpath
SHA-256へ固定し、別の明示承認を受ける。repository scannerは、その完全一致行と
完全一致pathだけをrepository内で許容する。変更行、追加出現、別path、wheel、
sdist、Skill archiveでは常に拒否する。したがってPhase 1E計画はE2Eの
non-packageable test
specificationであり、実CAE artifactのGit格納を許す例外ではない。

## 3. Scope boundary

Phase 1が実装するもの:

- 既存FEBのread-only inspectionと参照閉包検査
- STEPのread-only inspection
- exact source resolutionとimmutable provenance
- 解析意図契約、revision、nonce-bound人間承認
- case state、append-only event、lock、attempt、resume key
- FEBio 4.12 owned execution
- LOG解析と公式FBS bridge
- 複合completion gateとintent-specific result validation
- 失敗分類、解析意図に照らした変更提案
- 契約で明示承認された数値制御だけのdiagnostic retry
- deterministic report
- Codex専用Skill、wheel、installer、rollback
- 合成実機testとBottomFrame実モデルE2E

Phase 1が実装しないもの:

- STEPからの完全な物理モデル生成
- 自動再メッシュまたは局所メッシュ修正
- 材料、荷重、拘束、接触、要素形式、形状の自動変更
- FEBio dumpからの途中再開
- Codex以外のLLM adapter
- MCP server
- 正常終了だけを根拠にした設計合否
- 製品強度、材料校正、製造妥当性の証明

したがってPhase 1でNegative Jacobianが発生した場合、Codex Case Agentが
承認済み解析意図、ROI、保護形状、荷重経路、接触、失敗時刻・要素証跡を使って
原因と変更候補を説明する。しかし局所メッシュ変更は適用せず、
`INTENT_IMPACT_REVIEW`または`WAITING_FOR_HUMAN`で停止する。解析意図を保った
自動局所修正はPhase 3の対象である。

## 4. Responsibility for debugging

デバッグの責任主体は、同じ解析意図契約を所有するCodex CAE Case Agentである。
無文脈のsolver fixerへ丸投げしない。補助agentを使う場合も、次を明示入力として
渡す。

- approved intent revisionとapproval record
- source/input/model/tool hashes
- semantic entity map
- ROI、保護形状、荷重経路、不変条件
- failure class、phase、fingerprint
- LOG/FBS/mesh/geometry evidence bundle
- retry historyと残りbudget

Codexの判断だけで安全性を成立させない。CLIは、承認範囲外変更、古い承認、
同一retry、証跡不足、禁止状態遷移、永久領域への早期昇格を拒否する。

修正候補は必ず次を含む。

- 原因仮説と証拠
- exact変更対象とbefore/after
- ROI、保護形状、荷重経路、接触、拘束への影響
- intent impact分類
- 改善予測と副作用
- 検証方法
- 自動実行可否
- rollback方法

分類は`INTENT_PRESERVING`、`INTENT_SENSITIVE`、`INTENT_CHANGING`の3つとする。
自動実行可能なのは、approved contractのclosed numerical policyにexact matchし、
`purpose="diagnostic"`かつ`eligible_for_promotion=false`で、monotonic guardと
retry budgetを満たす新attemptだけである。

## 5. Human authority and source authority

重要な物理条件が不足している場合は推測せず停止する。承認は、purpose、
analysis ID、revision、contract SHA-256、input-record-set digest、
source-selection approval digest、execution-profile SHA-256、nonceへbindする。
contractの`expected_run.execution_request_sha256`は、closed external
execution request全体のcanonical SHA-256をbindする。
Codexは承認文を生成しない。過去の会話中の`OK`を新しい承認要求へ流用しない。
execution-profile SHA-256はinstalled release/profile/policy/FBS runtimeだけでなく、
そのrevision専用`intent-inspection-binding-rNNNN.json`のbytesにもbindする。
したがって承認要求後のFEB inspectionまたはdomain selectionの変更は承認を失効させる。
preflightとreportは、intent approval recordのpath/request ID/SHA-256をimmutable
`INTENT_APPROVED` eventへ照合する。source-selection approvalも同様にrecord
path/SHA-256/candidate-set/selected candidateを
`SOURCE_SELECTION_APPROVED` eventへ照合してからingestまたは報告する。

このbindingは監査可能性を提供するが、発言者本人を暗号学的に認証するものでは
ない。この制限をREADME、Skill、reportへ表示する。

外部sourceはpathだけで信用せず、bytesとSHA-256を一致させる。preferred pathが
移動した場合、代替候補が1件でもseparate source-selection approvalを要求する。
prohibited predecessorは選択できない。類似FEBは参照知識であり、権威ある入力と
hashが一致しない限りattempt outputへ転用しない。

`source-expectation`のcanonical top-level fieldsは次で固定する。

```text
schema_version = 1
role
preferred_path
expected_sha256
expected_bytes
search_roots
prohibited_candidates
lineage_evidence
reference_results
lineage
```

`lineage_evidence` itemは`kind/canonical_path/sha256/bytes`、
`reference_results` itemはさらに`reuse=false`を持つ。
公開`source-resolution` evidenceはclosed schemaとし、candidateのpath fieldは
`path`で固定する。再開時decoderだけがこの`path`を
`SourceCandidate.canonical_path`へ明示変換し、candidate-set digest、
membership、bytes、SHA-256、modified timeを再検証してからingestする。

## 6. Canonical cross-plan interfaces

後続planは次のcontractを別名で再実装しない。

| Concern | Canonical contract |
|---|---|
| Command status | lowercase `success`, `waiting_for_human`, `error` |
| Exit codes | `0, 10, 20, 30, 40, 50, 60, 70` |
| Evidence | `EvidenceRecord(kind, data)` serialized as closed `{"kind","data"}` |
| One mutation | `case.append(event_type, to_state, payload)` |
| Locked mutations | `with case.locked() as transaction:` then `transaction.replay()` / `transaction.append(...)` |
| Reopen adopted case | `CaseStore.open(case_dir)` |
| First legacy adoption only | `CaseStore.adopt_existing(...expected_manifest_sha256, preexisting_inventory_json, expected_preexisting_inventory_sha256)` |
| FEB file inspection | `inspect_feb(path, excluded_domains=())` |
| In-memory FEB inspection | `inspect_feb_bytes(data, source_name, excluded_domains=())` |
| Invariant signatures | closed keys `domain/material/reference_closure/load/boundary/contact/output` |
| Model evidence | `create_model_evidence(..., excluded_domains=())` stores the canonical exclusion set, applies it only to the domain signature, and retains full-mesh counts/connectivity/domain bindings |
| Persisted source resolution | closed `source-resolution` schema; public candidate `path` decodes explicitly to `SourceCandidate.canonical_path` |
| Approval execution profile | installed release/profile/policy/FBS identities plus the exact revision-specific intent-inspection-binding SHA-256 |
| Approved execution request | `expected_run.execution_request_sha256` equals the canonical SHA-256 of every external request field |
| Approval event binding | current intent/source approval record path and SHA-256 must match their immutable approved events |
| Attempt release identity | resume key and `tool-fingerprints.json` bind harness version, build provenance, install manifest, wheel, reviewed source commit, solver, FBS/profile, and policy |
| Attempt generated artifact | `AttemptStore.create_generated(name, data)` |
| Run ownership | immutable per-attempt created/bound/cancel/cleared lease records |
| Solver artifact roles | exactly `attempt-solver-log`, `attempt-solver-xplt` |
| Solver evidence kinds | `process-evidence`, `log-verification`, `fbs-verification`, `completion-decision`, `result-validation` |
| Completion request binding | `decide_completion(..., fbs_request: XpltRequest \| None)`; `None` adds `FBS_REQUEST_UNAVAILABLE` and can never reach `SOLVED` |
| Result promotion | caller supplies no claimed status; `promote_verified(attempt)` replays authoritative state and evidence under lock |
| Report promotion | complete JSON/HTML bundle becomes visible create-new; only then transition to `REPORTED` |
| Installed identity | one `installed-identity` evidence record binds release/package/wheel/build/Skill/FBS/profile plus exact wheel/sdist/Skill-archive pointers |

`CASE_MANIFEST.json`だけを現在状態のprojectionとしてatomic replaceできる。
event logと全persistent evidence/artifactはappend-onlyまたはcreate-newとし、既存の
異なるbytesを上書き・削除しない。solver実行中は長時間case lockを保持せず、
immutable run leaseで所有権を証明する。

## 7. State and success contract

正常系:

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

主な停止・失敗状態:

```text
WAITING_FOR_HUMAN
MODEL_FAILED
MESH_REJECTED
SOLVE_FAILED
RESULT_INCOMPLETE
INTENT_IMPACT_REVIEW
CANCELLED
```

FEBio process exit code 0、LOG存在、XPLT存在のいずれか単独では`SOLVED`にしない。
少なくとも次をすべて要求する。

1. owned process treeが正常終了した。
2. LOGとXPLTが今回のattemptでfreshに生成された。
3. LOGがnormal termination、期待step、最終timeを示す。
4. fatal、missing reference、Negative Jacobian、unresolved warningがない。
5. XPLTをofficial FBS runtimeで読める。
6. expected state/timeとrequired fieldsが存在し、非空かつ有限である。
7. model、mesh、geometry、kinematicsのintent-specific validationが通る。

INIT-only output、stale XPLT、読めないFBS、field association ambiguity、
transitive hash driftは成功ではない。

## 8. Test and release gates

Phase 1Dまでに次をgreenにする。

- unit tests: schema、state、path、hash、inspection、policy、parser、patcher
- contract tests: installed resources、CLI JSON、FEBio/FBS profile
- integration tests: approval、attempt、lease、runner、promotion、orchestration
- synthetic real-tool tests: success、INIT-only、Negative Jacobian、timeout、
  cancel、missing output、FBS unreadable
- Codex Skill RED/candidate/installed evaluation campaigns
- wheel build、clean-venv install、installed-only tests、rollback
- repository boundary scan and legacy allowlist enforcement

Codex evaluationは実際に利用可能な非対話Codex実行面をpreflightし、executable
path/hash、version、command shape、authentication readinessを記録する。利用可能な
実行面がない場合はSkillを作成する前に
`CODEX_EVAL_SURFACE_UNAVAILABLE`でfail-closedとする。これは他LLMへの依存ではなく、
Codex専用Skillを実Codexで評価するためのrelease gateである。

package、Skill、runtime lock、build provenance、evaluation summaryを一つの
`release_id`へbindする。Phase 1EはPATH上のeditable checkoutではなく、Phase 1Dが
installed identityとして検証したCLIだけを使用する。

## 9. BottomFrame final E2E

最終E2Eの唯一の実行手順はPhase 1E planとする。対象case:

```text
C:\Users\backo\OneDrive\Documents\FEBio\02_CAE\01_Active\
Bottom_Frame\2026-07-30_0729C_local040-screw
```

Phase 1Eは次を必須とする。

- 既存case全ファイルのpreexisting inventoryを初回write前に固定する。
- 現行aligned FEBをexact path/bytes/SHA-256で選択する。
- superseded predecessorをprohibited candidateとする。
- 過去LOG/XPLTは`reuse=false`の参照証跡に限定する。
- ABS Tet10、材料、domain、surface pair、BC、rigid constraint、controller、
  output reference、剛体ネジ軸/寸法/変位を検査する。
- `allowed_numerical_changes=[]`のintentと`retry_budget=0`の実行設定を作る。
- freshな人間承認を要求し、そのturnで停止する。
- installed CLIで新attempt、新solve、新FBS verification、新reportを作る。
- 20固定step、最終time、剛体節点変位populationを検証する。
- 新規成果物とpreexisting inventory非改変を監査する。
- CAE成果物がGitへ入っていないことを検証する。

このE2Eはヘッドレス非線形接触workflowの監査可能な完走を検証する。ABS材料の
校正、実ねじ山、摩擦、製造ばらつき、製品強度の妥当性は証明しない。

## 10. Program completion

Phase 1 completionを宣言できるのは次の全条件を満たした場合だけである。

1. Phase 1A–Dの全Task commitと全verification commandがgreen。
2. clean wheelからinstalled-only CLI/Skill/FBS identityを再現できる。
3. Codex Skill installed campaignがcandidate campaignと一致し、boundary違反がない。
4. synthetic real FEBio/FBS matrixがgreen。
5. Phase 1Eのfresh human approval後のBottomFrame runがgreen。
6. solver LOG、XPLT、FBS evidence、result validation、report、inventory auditが
   hash-boundである。
7. real CAE bytesがtool repository/Git/GitHubへ入っていない。
8. tool worktreeがcleanで、release recordが実装commitと一致する。

完了報告は、通過したgate、exact artifact paths/hashes、solver/FBS状態、
mesh/model/kinematic evidence、制限事項を示す。未実施または失敗したgateが1つでも
あれば「Phase 1 completed」と表現しない。
