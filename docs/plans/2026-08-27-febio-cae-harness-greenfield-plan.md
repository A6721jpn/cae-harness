# FEBio CAE Harness V2 グリーンフィールド開発計画

## 1. 方針

正本:
[Codex向けFEBio CAE Harness V2 最小仕様](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md)

空のsource treeと新しいGit履歴から開発する。初期repositoryには正本2文書と新規scaffold
だけを置き、各機能を新しいtestと実装として作る。

実CAEデータは`02_CAE`に残し、tool repositoryへ入れない。

## 2. 開発単位

### Phase 0 — 新規repository

- 空のPython 3.12 package `febio_cae_harness`を作る。
- CLI entry pointを`febio-cae`とする。
- pytest、format、type check、boundary scanを設定する。
- 新しいtool-only GitHub repositoryを接続する。
- 最初のclean buildとinstalled smokeを通す。

完了条件: 空のpackageがbuild、install、`febio-cae --version`まで再現できる。

### Phase 1 — Case、intent、workspace

- case作成と`ValidatedCaseWorkspace`を実装する。
- intent JSON、attempt、append-only event、manifestを実装する。
- `GATHERING`、`BOUND`、`ASK_AND_BLOCK`を実装する。
- Case AからCase B、tool root、`02_CAE` rootへのwriteを拒否する。

完了条件: 合成caseでstate再開、改ざん拒否、原本不変を確認できる。

### Phase 2 — 完成FEBのheadless解析

- FEB inspectionとreference closureを実装する。
- derived FEB、preflight、FEBio supervisorを実装する。
- LOG、公式FBS、result、report検証を実装する。
- timeout、cancel、INIT-only、missing outputを試験する。

完了条件: 小型FEBがinstalled CLIだけでsolveからreportまで完走する。

### Phase 3 — 自律デバッグと入力拡張

- failure分類、修正proposal、retry ledgerを実装する。
- Negative Jacobianと非線形収束の診断を実装する。
- 不完全FEBの不足質問と補完を実装する。
- STEP inspection、mesh、FEB生成を実装する。

完了条件: 3種類の入力が意図契約を通り、物理条件を推測せず解析できる。

### Phase 4 — fallbackと切断耐性

- 公式Computer Use経路を実機確認する。
- FEBio Studio fallbackと出力再検査を実装する。
- client切断中のsolver継続と再接続を実装する。

完了条件: Studio操作がheadless不能時だけ使われ、client crash後もsolver結果を回収できる。

### Phase 5 — build、shortcut、最終E2E

- 外部build scriptと`latest-development`へのatomic stageを実装する。
- Start Menu shortcutを固定folderへ登録・検証する。
- complete/incomplete/STEP/debug/fallback/crashのE2Eを順番に実行する。
- 最後にBottomFrame剛体ネジ非線形解析を実行する。

完了条件: 全E2E、実行identity、repository boundary、clean statusが合格する。

## 3. 並列開発

Orca上でLuna workerを小さなtaskに分ける。Phase 1の共通contractを一人が固定した後、
FEB inspection、solver/FBS、intent/autonomy、build/launchを別worktreeで並列実装できる。

各workerは次を残す。

- base commitと担当file
- RED/GREEN test commandとexit code
- commit SHA
- 未検証事項
- clean worktree

未コミット差分や巨大な統合snapshotをhandoffにしない。review済みの小さなcommitだけを
integration branchへ取り込む。

## 4. 開発ルール

- 実装、test、commit、feature branchへのpushは逐次承認なしで進める。
- 物理条件が証拠から決められない場合だけ質問する。
- 合成testを実FEBioまたは実Studioの成功と表現しない。
- 中断、collection error、環境errorをgreen件数に含めない。
- 実モデル、result、credential、Codex Desktop stateをcommitしない。
- 各Phase終了時にclean commit、test結果、制限事項を記録する。

## 5. 完成判定

最小仕様の必須E2Eがすべてfresh evidence付きで合格し、BottomFrame最終E2E、固定shortcut、
GitHub boundary、clean source commitを確認できた時点で完成とする。
