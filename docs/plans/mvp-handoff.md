# MVP実装の引き継ぎ（実装担当LLM向け）

作成日：2026-09-15。参照順序：[開発契約](../../AGENTS.md) → 本書 → [計画書 §2](2026-09-14-febio-cae-harness-greenfield-plan.md) → 必要箇所のみ[設計仕様書](../specs/2026-09-14-febio-llm-cae-harness-design-v2.md)および[実装ノート](../specs/implementation-notes.md)。

本書は計画書のタスクT1〜T6について、対象ファイルと具体的な変更箇所まで落とし込んだ作業指示書である。設計判断は計画書および仕様書を正とし、本書の記述と相違がある場合はそちらに従う。

## 1. 作業場所と環境

| 項目 | 値 |
|---|---|
| ブランチ | `orca/acceptance-integration`（2026-09-20再開時は `V2` と同じ `af3a248`） |
| 作業ツリー | `<WORKTREE>`（Orca管理ワークツリー。実パスはGit外の実行記録だけに保存） |
| 基準コミット | `af3a248`（2026-09-20再開基準。T2の実装 `c5df325` を含む） |
| Python | 3.12.10。`.venv/`（作成済み、Git管理外）に `pip install -e ".[dev,native]"` 済み |
| FEBio 4.12.0 | `<FEBIO4_EXE>`（実体・SHA-256はGit外のゲート記録で照合） |
| FEBio Studio | `<STUDIO_EXE>` |
| Gmsh 4.15.2 | `.venv` に pip で導入済み（`import gmsh`、`.venv\Scripts\gmsh.bat`） |
| 環境変数 | `.env.example` を参照。`FEBIO_CAE_FEBIO_PATH` / `FEBIO_CAE_STUDIO_PATH` / `FEBIO_CAE_GMSH_PATH` |

```powershell
Push-Location '<WORKTREE>'
& '<WORKTREE>\.venv\Scripts\Activate.ps1'
$env:FEBIO_CAE_FEBIO_PATH = "<FEBIO4_EXE>"
$env:FEBIO_CAE_STUDIO_PATH = "<STUDIO_EXE>"
$env:FEBIO_CAE_GMSH_PATH = "$PWD\.venv\Scripts\gmsh.bat"
febio-cae doctor --json          # 3ツールとも FOUND_UNVERIFIED になること
python -m pytest -q              # 標準試験（unit + component）
python -m ruff format --check . ; python -m ruff check . ; python -m mypy src tests
```

合成入力（Git管理外、開発機上に配置）：

| 用途 | パス |
|---|---|
| 合成STEP（直方体） | `<SYNTHETIC_STEP>` |
| 準備要求3件（粗・中・細） | `<PREPARATION_REQUESTS_DIR>\{coarse,refined,fine}.json` |
| E2E設定の実例 | `<SETTINGS_JSON>` |
| 旧承認バンドル（現在は不要。参考のみ） | `<LEGACY_BUNDLE>` |

新たな合成STEPが必要な場合は `.local/synthetic/` に生成する。実CAEモデルや `02_CAE` には手を触れない。
### 2026-09-21 現在の受け入れ境界

#### 証拠root

| 記号 | 実体（Git管理外） |
|---|---|
| `<COORDINATION>` | 元のチェックアウト/.local/coordination |
| `<ROOT_PYTEST>` | 元のチェックアウト/.local/pytest-basetemp |
| `<WORKTREE_PROOFS>` | Orca開発ワークツリー/.local（docs-proofのみ） |


#### 状態とartifact map

| 区分 | authoritative artifact / ID | 状態・会計 |
|---|---|---|
| 現行candidate／履歴 | current source=`71c388f229dbfabc9fffb48c90c4ca9c1def5ee9`、test-only=`56e122b`、format-only=`0e35ba4d`、native final wheel SHA-256=`f978de60803f70b9a5858a60bae185e0ed467949b0e062eafd2826a5e18b2cdd`／size=`404193`。251/b28、71/c180、過去1797/1は履歴。56e122b最終標準full-suiteはexit 0（1798 passed／0 failed／3493.86 s）、native-enabled finalは22 stage全exit 0、pytest 1 passed／942.46 s、両run `SUCCEEDED`／必須5 numerical statuses `PASS`、baseline `COMPLETE`／candidate comparison |
| canonical acceptance status | [検証記録](../reviews/2026-09-20-mvp-planar-e2e.md)、`<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/final-accounting.json` | 合格済みsource＋native candidateは受理対象として記録。manual preparationは`FAILED`／`ABORTED/BLOCKED`でmanual 8が残るため、MVP全体完了は宣言しない。詳細はcanonical reviewを正とする |

#### 全体会計と残件
既存raw reportとfinal cumulative accountingに基づくcross-flow inspectionは11 requests＝historical 9＋current environment failure 1＋current successful native flow 1、observed success childは7（初期3要求のchild有無`UNKNOWN`）。named-flowはnative inspection成功の観測5件＋環境失敗要求1件の6 requests、observed child 5（missing-Gmshはchildなし）、named-flow solverは8、Studioは4、successful preparationは4＋manual owner failure 1。native総child数や完全ledgerを推論せず、[検証記録](../reviews/2026-09-20-mvp-planar-e2e.md)のcanonical tableを正とする。full-suite歴史的試行は非PASS、71c388f postfix標準full-suiteもexit 1非PASS、56e122b最終標準full-suiteはexit 0 PASS。native-enabled final automated 8-stepは22 stage全exit 0、必須5 numerical statuses PASS、baseline COMPLETE／candidate comparisonである。

## 2. 現状の最重要事実

MVP の手順2（対応表登録）は 2026-09-15 に修正済みである。251版の自動T5（履歴）は上記flowまで完了、T6 manual記録はpreparation failure／MVP blockとして[検証記録](../reviews/2026-09-20-mvp-planar-e2e.md)に固定した。71c388f postfix標準full-suiteはexit 1非PASS、56e122b最終標準full-suiteはexit 0 PASS、native-enabled final automated 8-stepはgmsh 4.15.2でpytest 1 passed／942.46 s、22 stage全exit 0、両run `SUCCEEDED`／必須5 numerical statuses `PASS`、baseline `COMPLETE`／candidate comparison、raw label `NUMERICAL_GATE_PASSED_NOT_OVERALL`は原因推定なし。251/b28の証拠を現行candidateへ付け替えない。
### 現行gateと標準診断

static／build／focused pytestの実コマンドと終了値、22 stageの実argv、accepted sourceの変更ファイル一覧は[検証記録](../reviews/2026-09-20-mvp-planar-e2e.md)に集約する。歴史的full-suite wrapperは`INTERRUPTED_TIMEOUT`／非PASS、71c388f postfix標準full-suiteはexit 1非PASS、56e122b最終標準full-suiteはexit 0 PASS。runner setup／missing-Gmsh corrected flowは別記録で保持し、native-enabled final automated 8-stepはbaseline `COMPLETE`／Studio `LAUNCHED`／candidate comparisonまで取得、自動gate PASSである。MVPで残る必須記録はmanual 8である。


## 3. タスク別の作業指示

### T0：静的・buildゲート

実E2E実施版251/b28のruff、mypy、scan、build、focused pytest結果、および修正版71c388fのstatic／focused clean結果はcanonical reviewに固定済み。古い失敗件数やpytest cacheの並びを現行修正版のfull-suite結果と混同しない。

### T1〜T4：自動flowの現行契約

汎用`run`、built-in profile、mesh品質の`UNVERIFIED`扱い、Studio `LAUNCHED`／対象XPLT読込は実装済み。画面タイトル3.1.0とreceipt version `UNVERIFIED`を区別し、既存のCONFIRMED経路も実装として保持する。251版の自動E2E（履歴）の証拠を現行修正版SHAへ付け替えない。

### T3：標準2F修復

source-local consumerではmissing-receiptを`UNVERIFIED`とする。共有exceptがcanonical global IDを返してlocal宣言IDを失う不備は修正版71c388fで修復済み。71c388f postfix標準full-suiteはexit 1非PASS、56e122b最終標準full-suiteはexit 0 PASS。runner setup／missing-Gmsh corrected flowは履歴停止、native-enabled final automated 8-stepは22 stage全exit 0、inspection1／preparation1／solver2／Studio1、必須5 numerical statuses `PASS`、baseline `COMPLETE`／candidate comparisonまで取得済みである。今回の追加実行は不要、manual次操作はbudget判断待ち、V2 integrationは明示的ユーザー指示待ちとする。詳細はcanonical reviewを正とする。

### T5：一貫試験

現行native-enabled final automated 8-stepはgmsh 4.15.2で22 stage全exit 0、両run `SUCCEEDED`／必須5 numerical statuses `PASS`、baseline `COMPLETE`／candidate comparisonまで取得した。比較値とlatest 22-command ledgerはcanonical reviewに記録済み。manual 8がMVPで残る必須記録であり、manual caseはretry／reset／new caseを行わず保持する。

### T6：文書と記録

1. 56e122b最終標準full-suite PASS（1798 passed／0 failed／3493.86 s）を受け、native-enabled final automated 8-stepは既定budgetで1 flow実施済み。gmsh 4.15.2でpytest 1 passed／942.46 s、22 stage全exit 0、inspection1／preparation1／solver2／Studio1、両run `SUCCEEDED`、必須5 numerical statuses `PASS`、baseline `COMPLETE`／candidate comparisonを記録した。今回の追加実行は不要、manual `case-6294a0a9e02c`の次操作はbudget判断待ち、V2 integrationは明示的ユーザー指示待ちとする。manual caseはretry／reset／代替caseなしで保持し、manual 8を残件とする。[canonical review](../reviews/2026-09-20-mvp-planar-e2e.md)を正とする。


## 4. やらないこと

- 球、円柱、摩擦、Neo-Hookean、ソースローカル細分化、LLM経路には手を加えない（backlog）。
- `adapters/geometry/_gmsh_runtime.py`（約4,200行の依存関係認証）には手を触れない。正常に動作しているならそのまま利用する。
- 新たなハッシュ固定、自己ハッシュ、追加の権限レイヤーは導入しない。既定対応表の組み込み（対応済み）によって撤去したハッシュ固定は復活させない。
- 試験は変更した契約の分のみ作成する。1機能につき数本で十分である。

## 5. 報告の形式

タスクごとに次の項目を報告すること：対象コミットのSHA、変更したファイル、実行したコマンドと終了コード（`pytest` の件数）、実FEBio／Studio の起動回数、未検証事項、次回必要な判断事項。中断・省略した試験は合格数に含めない。
