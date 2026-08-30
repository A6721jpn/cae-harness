# Codex向けFEBio CAE Harness V2 最小仕様

## 1. 正本

- Status: authoritative greenfield specification
- Date: 2026-08-27
- Target: Windows、Codex subscription、FEBio 4.12
- 実装計画:
  [V2グリーンフィールド開発計画](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md)

開発は空のsource treeと新しいGit履歴から開始する。初期repositoryは本書、上記計画、
新規scaffoldだけで構成し、すべてのコード、schema、test、Skill、buildを本仕様から作る。

実CAEモデルと結果は`02_CAE`に置き、GitまたはGitHubへ入れない。

## 2. 目的

人間がFEBまたはSTEPと自然言語の解析目的を与えると、Codexが解析意図を理解し、
FEBioモデルの構築、ヘッドレス実行、デバッグ、結果検証、報告までを行う。

最初の数回の会話で物理条件を確定した後は、解析意図の範囲内でモデル修正と再解析を
自律的に進める。材料、荷重、拘束、接触、ROIなど、結果を左右する条件が不足する場合
だけ質問して停止する。

## 3. 対応入力

| 入力 | 動作 |
|---|---|
| 完成FEB | 検査後、原本を変更せずderived modelで小改造・再解析する |
| 不完全FEB | 存在する条件と不足条件を分け、不足する物理条件だけ質問する |
| STEP中心 | 形状を検査し、明示された物理条件からmeshとFEBを構築する |
| 明示された類似FEB | 互換性と来歴を確認できた項目だけ参照する |

類似モデルを自動探索しない。STEPの形状から材料、荷重、拘束、接触を推測しない。

## 4. 最小構成

```text
User
  -> simple CLI/conversation interface
       -> isolated Codex CAE Case Agent
            -> CAE Harness
                 -> case/intent/evidence store
                 -> model and mesh adapters
                 -> durable FEBio supervisor
                 -> LOG/FBS/result validators
                 -> FEBio Studio fallback
```

- 初期版に専用GUIは作らない。
- Codex Desktop AppまたはOrcaを製品runtimeに使わない。
- Orcaは開発時のagent、worktree、review管理だけに使える。
- Codexは公式Codex CLIのsupported interfaceとsubscription loginを使う。
- CAE専用のCodex home、会話、case workspaceを使用し、Desktopの履歴をimportしない。

## 5. 解析意図と自律運転

caseごとに次をJSONとして保存する。

- 工学的な問い
- 単位、材料、荷重、拘束、接触、解析step
- ROI、荷重経路、評価量
- 保護する形状と不変条件
- 許容するmesh・数値変更と上限
- retry、時間、CPU、memory予算
- 条件の出典と未確定事項

状態は次の3つでよい。

```text
GATHERING
BOUND
ASK_AND_BLOCK
```

`BOUND`では契約範囲内の操作を追加承認なしで実行する。条件が不足する、または物理を
変える必要がある場合は`ASK_AND_BLOCK`にする。回答は修正案への承認ではなく、新しい
解析条件として保存する。

## 6. デバッグ

デバッグは同じ解析意図を保持するCodex CAE Case Agentが担当する。各修正は次を記録する。

- 原因仮説と証拠
- 変更前後の差分
- 影響するmesh、材料、荷重、拘束、接触
- ROIと荷重経路への影響
- 検証方法、結果、rollback方法

修正を次に分類する。

| 分類 | 動作 |
|---|---|
| `INTENT_PRESERVING` | 契約範囲内で自動適用する |
| `INTENT_SENSITIVE` | 結果差が許容内と検証できる場合だけ自動適用する |
| `INTENT_CHANGING` | 自動適用せず、必要な物理条件を質問する |

Negative Jacobianでは、初期meshか変形途中か、step/time、要素位置、全積分点Jacobian、
周辺品質、ROI・接触・拘束との関係を確認する。局所修正後は形状差、mesh品質、荷重経路、
反力、変位、接触、評価量を比較する。solverが通ったことだけを採用理由にしない。

同じ失敗を繰り返す場合、改善がない場合、またはretry予算を超えた場合は停止する。

## 7. Caseと証拠

```text
02_CAE/<case>/
  CASE_MANIFEST.json
  01_Input/
  02_Model/
  03_Result/
  04_Report/
  05_Verification/
  90_Temporary/attempts/<attempt-id>/
```

- 入力原本を上書きまたは削除しない。
- 変更とsolver実行はattemptごとに分離する。
- case、intent、attempt、artifactをSHA-256で結ぶ。
- eventはappend-onlyとし、manifestは現在状態のprojectionとする。
- formal writeは検証済みcaseの`90_Temporary`だけに許可する。
- 検証済み成果物だけを永久領域へcreate-newで昇格する。

## 8. Solverの継続

FEBioは画面や会話clientとは別のsupervisorが起動・監視する。supervisorはcase、intent、
attempt、solver executable、PID、process開始時刻、LOG/XPLTを記録する。

clientが閉じるかcrashしても、開始済みsolverは継続する。切断中は新しいmodel変更やretryを
開始しない。再接続時にcase、attempt、process、eventを照合して監視を再開する。

cancelはcase-owned commandから対象process treeだけに行う。PIDを直接指定する操作を
正式経路にしない。

## 9. 成功条件

次をすべて満たした場合だけ解析成功とする。

1. 所有されたFEBio processが正常終了した。
2. 今回attemptのLOGとXPLTが新規生成された。
3. LOGがnormal termination、期待step、最終timeを示す。
4. fatal、missing reference、Negative Jacobianがない。
5. XPLTを公式FBSで読み取れる。
6. 必要な結果fieldが存在し、有限値である。
7. mesh、全積分点Jacobian、ROI、評価量が解析意図を満たす。
8. reportと全証拠がcase、intent、attemptへ結び付いている。

終了コード、LOG、XPLTの存在、合成adapterの成功のいずれか単独では成功にしない。

## 10. FEBio Studio fallback

通常経路はヘッドレスとする。ヘッドレスで実行できないFEBio Studio機能が必要な場合だけ、
FBSを起動して公式Computer Useを使う。

操作前後の画面、入力、操作、保存物、差分を記録する。出力は再検査してattemptへ取り込み、
その後ヘッドレス経路へ戻す。公式経路、runtime identity、証拠化を確認できない場合は
利用しない。

## 11. Buildと起動

- source、test、docsは新しいroot commitを持つtool-only GitHub repositoryで管理する。
- 初期repositoryは正本2文書と新規scaffoldだけを含む。
- 外部build scriptがclean commitからtest、package、smokeを実行する。
- 成功時だけ`%LOCALAPPDATA%\FEBioCaeWorkbench\latest-development`へatomic stageする。
- Start Menu shortcutは常にこの固定folderのlauncherを指す。
- 起動時update、network確認、version別folder選択は行わない。
- 起動中のcommitとbuild identityを表示またはCLIで確認できるようにする。

## 12. 必須E2E

1. 完成FEBの小改造、solve、FBS、report
2. 不完全FEBの不足質問と補完
3. STEPからmesh、FEB、solve、report
4. Negative Jacobianの意図保全修正
5. Studio fallbackとヘッドレス復帰
6. client crash中のsolver継続と再接続
7. clean buildと固定shortcut起動
8. 最後にBottomFrame剛体ネジ非線形解析

BottomFrame E2Eは`02_CAE`の権威ある入力を使い、既存成果物を変更せずfresh attemptで行う。
実行前にFEB、物理条件、ABS Tet10、剛体ネジ、接触、2.0 mm変位、20固定stepを確認する。

## 13. 完成条件

上記E2Eが実FEBio、公式FBS、実case証拠で合格し、tool repositoryがclean、shortcutの
実行identityがbuild commitと一致し、実CAEデータがGitHubに0件の場合だけ完成とする。

未実施、合成試験だけ、途中中断、環境エラー、未回収結果を完成に含めない。

## 14. Product trust boundary (Trust Model A)

The shipped `febio_cae_harness` code and its Python runtime are the product trusted
computing base (TCB). The headless CLI does not load or execute untrusted or
user-supplied Python or plugins in-process.

The untrusted boundaries remain CAE input files, filesystem names and artifacts and
their concurrent replacement, child processes, FEBio/FBS outputs, crash or reconnect
state, and external runtime identity. Python code already executing inside the trusted
process with arbitrary object mutation, monkeypatching, `object.__getattribute__`,
ctypes/native-memory access, debugger or process injection, or replacement of package
internals is process compromise and is outside this product security contract.

Python-private issuance, latch, and registry state is a correctness and tamper-
detection mechanism, not an unforgeable native authority boundary. Existing fail-closed
consistency checks must remain in force. This trust-boundary decision narrows only
same-process hostile-code claims; it does not weaken OS, file, process, or FBS evidence
requirements. Synthetic FBS evidence remains synthetic-unverified with `official=false`
and must not be presented as official FBS or real integration success.
