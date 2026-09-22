# FEBio CAEハーネス V2 — 実装・検証計画

文書版：0.5／作成日：2026-09-07／改訂日：2026-09-15。
ファイル名の日付は識別子である。本書は工程・検証・進捗を、[設計仕様書](../specs/2026-09-14-febio-llm-cae-harness-design-v2.md)は製品の振る舞いを、[実装ノート](../specs/implementation-notes.md)は現行実装の詳細をそれぞれ定める。開発体制および証拠に関する規則は[開発契約](../../AGENTS.md)に規定されている。

## 1. 到達点と開発方針

STEP部品と新規剛体治具の接触押し込みを実FEBioで解析し、その結果を検証してFEBio Studioに表示する。この一連の処理経路に対して、条件入力、部分編集、再解析、および比較の各機能を接続する。

目的・制約・完了条件を中心に記述し、詳細な実装手順の判断は担当者に委ねる。初回の実装にとどまらず、許可された範囲内での実行・確認・修正までを完遂する。不具合に対しては再現性のある回帰試験を用意し、通常の変更時には影響を受ける契約を検証する。一律のテスト先行、テストと実装の機械的なコミット分離、ならびに固定的な試験件数の設定は求めない。ただし、最終候補に対する必須検証、独立レビュー、実ツール試験、および実モデル試験は維持する。

使い捨ての合成データを用いたローカル環境での検証と修正は、継続して実施する。実ツールの実行は、登録済みの入力・条件・許可、および有限な予算の範囲内で行う。未確定の必須物理条件への対応、許可外の実データ操作、あるいは予算の拡張が必要な場合に限り、その判断を仰ぐ。通常の技術的判断を都度ユーザーへ差し戻すことはしない。

## 2. MVP（最初のゴール）の完了条件

設計仕様書 §1 に定めるMVPの対象範囲は、新規環境へ通常インストールしたwheelのCLIのみを用い、以下の手順が正常に通ることをもって完了とする。

| # | 手順 | 完了条件 |
|---|---|---|
| 1 | `case create` → `inspect --native` | 合成STEPを登録し、`INSPECTED` によりボディ・単位・体積を取得する |
| 2 | `provision-planar-profiles` | 組み込み既定の対応表による `PROVISIONED`（`--bundle-path` は省略） |
| 3 | `spec` | 明示条件の型付きJSONと根拠を登録する。形状・選択集合・メッシュの準備前に `READY` と判定しない |
| 4 | `prepare-planar` → `validate` → `freeze` | `PREPARED`、Tet10部品メッシュおよび直方体剛体メッシュを生成する。準備処理で検証・確定した版を、公開CLIでも `READY` かつ同一の不変版として確認する |
| 5 | `run` | 実FEBio 4.12で`run_status=SUCCEEDED`を確認し、結果一覧を取得・公開する |
| 6 | 品質 | 5項目の`PASS`、メッシュ依存性`UNVERIFIED`、`quality_status=PASS`を確認する |
| 7 | `preview` | Studioを起動して対象XPLTを開き、`LAUNCHED`、`task_status=COMPLETE` を実施する |
| 8 | ヤング率変更 → `run` → `compare` | 既存メッシュを再利用した子版の再解析と、反力―移動量曲線および部品変位の比較を行う |

MVPの完了は、`tests/e2e/test_installed_synthetic.py` への合格と、上記8手順を手動で実行した記録（`docs/reviews/`）によって証明する。なお、MVPの達成は球・円柱・摩擦・非線形材料・日本語入力・実モデルへの対応完了を意味しない。
### 現行状態（candidate 0e35ba4／source 71c388f／test 56e122b／format 0e35ba4／manual 8完了）

現行candidateはnative-enabled final flow `case-80e5f42a5043`で、gmsh 4.15.2、22 stage全exit 0、coarse／candidate両run `SUCCEEDED`、必須5 numerical statuses `PASS`、baseline `COMPLETE`、candidate comparison、same-mesh reuseを取得した。最終標準full-suiteは`1798 passed / 0 failed / 3493.86 s`である。MVP必須のmanual 8手順は2026-09-22にmanual final flow `case-bb9e975f3fb3`で完遂し、[manual 8実行記録](../reviews/2026-09-22-mvp-manual-final.md)を正とする。2026-09-21の旧manual `case-6294a0a9e02c`はpreparation `FAILED`／`ABORTED/BLOCKED`のまま履歴として保持する。automated flowと旧manual incidentの詳細なargv／exit／比較値／native・Studio・累積会計は[canonical review](../reviews/2026-09-20-mvp-planar-e2e.md)を正とする。251/b28、71/c180、過去1797/1は履歴節にのみ保持する。

### MVPまでの実装タスク

着手順は T0 → T1 → T3 → T4 → T5 → T6 とする。T2 は 2026-09-15 に完了済み。

| # | タスク | 内容 | 現在状態（2026-09-21） |
|---|---|---|---|
| T0 | ゲート基線の修復 | ruff、mypy、scan、build、focused pytestを現行sourceで確認する | 完了。static／build gate clean、focused 18 passed |
| T1 | 汎用 `run` | `prepare-planar`後の版を`case run --revision-id --solver [--preflight]`で実行する | 完了。現行native final `case-80e5f42a5043`でcoarse／candidate両run `SUCCEEDED`、solver dispatch 2 |
| T2 | 既定対応表の組み込み | built-in bundleを`provision-planar-profiles`で登録する | 完了（2026-09-15） |
| T3 | メッシュ依存性の任意化 | mesh dependenceが`UNVERIFIED`でも他5項目で`quality_status`を判定する | 完了。現行native finalで必須5 numerical statuses `PASS`、mesh dependence `UNVERIFIED`を記録 |
| T4 | `preview` の `LAUNCHED` | 登録済みStudioで対象XPLTを開き、起動receiptと対象を記録する。既存の`--window-id`／stdin `CONFIRMED`経路は実装として保持する | 完了。現行native finalでbaseline `COMPLETE`、Studio `LAUNCHED`、owned PID/creation/exe一致とinitial display PNGを記録 |
| T5 | 一貫試験の更新 | 8手順を一連の自動flowとして通す | 完了。現行native finalは22 stage全exit 0、baseline `COMPLETE`、candidate comparisonまで取得 |
| T6 | 文書と記録 | CLI実行例とmanual記録を保持する | **完了。** 2026-09-22のmanual final flow（新規case、公開CLI 22コマンド全exit 0、inspection1／preparation1／solver2／Studio1）で8手順を完遂し、[manual 8実行記録](../reviews/2026-09-22-mvp-manual-final.md)に記録した。旧manual preparationはFAILED／ABORTED/BLOCKEDのまま保持する。記録は2026-09-22にV2へff-only統合済み（pushは未実施）。履歴：71c388f postfix標準full-suiteはexit 1非PASS、56e122b最終標準full-suiteは1798/0/0/0 PASS、2026-09-21のnative-enabled final automated 8-stepはgmsh 4.15.2で22 stage全exit 0、pytest 1 passed／942.46 s、dispatch inspection1／preparation1／solver2／Studio1、両run SUCCEEDED、必須5 numerical statuses PASS、baseline COMPLETE／candidate comparison、raw label `NUMERICAL_GATE_PASSED_NOT_OVERALL`は原因推定なし |

## 3. 工程と現在地

| 工程 | 依存 | 成果と終了条件 | 状態（2026-09-21） | 根拠 |
|---|---|---|---|---|
| P0：骨格・互換性 | 本仕様 | Python 3.12、配布、版表示、環境診断。FEBio・Studio・Gmshの実体、バージョン、ハッシュ、入出力形式、および観測された能力の記録 | 完了（合成・単独調査） | `2026-09-07-p0-a-bootstrap`、`p0-b-*` 10件 |
| P1：共通契約 | P0 | ケース登録、草案・質問・不変版、単位、状態、世代一致更新、原子的保存を連携させ、共有スキーマ第1版を確定 | 完了 | `2026-09-07-p1-*` 15件、`2026-09-08-p1-interface-freeze`、`p1-registered-cli` |
| P2：形状・メッシュ | P1 | STEP調査、領域解決、球・円柱・直方体の生成、要素・面順、品質、ならびに再メッシュとキャッシュの検証 | 部分完了：平面、直方体、Tet10、公開調査、平面準備、細分化は実装済み。球・円柱・曲面近似は `UNVERIFIED`。球・円柱のネイティブ生成、ソースローカル細分化、Gmsh実行時識別の認証はコードが存在するものの、合成検証にとどまる | `2026-09-08-p2-*` 8件、`2026-09-09-public-native-inspection`、`public-planar-preparation`、`step-preparation-admission` |
| P3：解析・結果 | P1、P2 | 入力生成、所有プロセス管理、実FEBio、XPLT読み込み、数値照合、必須品質、Studio読み込み確認を連携 | 完了（現行native finalの合成flow）：22 stage全exit 0、両run `SUCCEEDED`／必須5 numerical statuses `PASS`、baseline `COMPLETE`、candidate comparison。Studio画面のinitial captureを取得し、last-state／deformation／full mesh／`CONFIRMED`は別表示境界として記録 | `<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/final-accounting.json`、[canonical review](../reviews/2026-09-20-mvp-planar-e2e.md) |
| P4：日本語操作 | P1、P3 | 意図・質問・回答・型付き差分を連携。根拠不足や競合を適切に処理し、実LLM接続により確認 | 部分完了・MVP対象外：`intent/answer/edit` のコードおよび実LLM試験（`tests/native/test_llm.py`）は存在 | `2026-09-09-p4-intent-source`、コミット `6a60b79`〜`6c4c7dd` |
| P5：変更・比較・復旧 | P2〜P4 | 元版を保存した状態での再解析、比較、キャッシュ無効化、有限回のリトライ、中断・改変・競合への対応を検証 | 部分完了：ヤング率変更、メッシュ再利用、3段階細分化、比較、および限定的な `cancel/resume` は実装済み | `2026-09-09-prepared-material-descendants`、コミット `d0cb891`〜`4039e9d` |
| P6：配布・合成一貫試験 | P0〜P5 | 新規環境へ通常インストールしたwheelから8手順と必須検証を行う | 完了（自動8手順）。native-enabled finalはgmsh 4.15.2でpytest 1 passed／942.46 s、inspection1／preparation1／solver2／Studio1、baseline `COMPLETE`／candidate comparison。manual 8はpreparation `FAILED`／`ABORTED/BLOCKED`で未完了。default `python -m build`／sdist-wheelと最終scanの証拠はcanonical reviewを参照 | [canonical review](../reviews/2026-09-20-mvp-planar-e2e.md) |
| P7：実モデル受け入れ | P6、使用許可 | 許可された実STEPおよび最終BottomFrameを用い、解析・品質・Studio・変更・再解析・比較に関する新たな証拠を取得 | 未着手 | — |

上記は現行native-enabled automated 8-stepまでを反映し、自動8手順は完了している。MVP全体で残る必須記録はmanual 8であり、candidate preview／Studio `CONFIRMED`は追加MVP要件ではない。raw report labelやmesh `UNVERIFIED`は別境界として記録し、原因を推定しない。詳細は[canonical review](../reviews/2026-09-20-mvp-planar-e2e.md)を正とする。

### Manual preparation incident（2026-09-21、read-only reconciliation済み）

T6 manual caseはpreparation `FAILED`／`ABORTED/BLOCKED`、duplicate impact `UNKNOWN`、root cause `UNDETERMINED`であり、後続validate／freeze／solver／Studio／compare／retry／new caseは行わない。詳細なevidence、lock／reservation／identity、flow分離は[canonical review](../reviews/2026-09-20-mvp-planar-e2e.md)に集約する。

2026-09-20の再開基準は`af3a248`とする。dev branch `orca/acceptance-integration`の受理・pushとV2 integrationは別管理であり、V2は現状のまま変更しない。V2への反映はAGENTSに従い明示的なユーザー指示がある場合だけ行い、自動fast-forwardや自動統合は約束しない。

2026-09-20のinstalled E2Eの過去候補失敗は履歴として保持し、latest ledgerとcanonical reviewを名前付きprovenanceの正とする。full-suite historical attemptは`INTERRUPTED_TIMEOUT`／非PASS、71c388f postfix標準1798-test full-suiteはexit 1非PASS（sole failure=`test_nested_optional_import_source_flow`）、56e122b最終標準full-suiteはexit 0 PASS（1798 passed／0 failed／3493.86 s）。runner setup／missing-Gmsh corrected flowは履歴として停止、native-enabled final 8-stepは22 stage全exit 0、両run `SUCCEEDED`、必須5 numerical statuses `PASS`、baseline `COMPLETE`／candidate comparisonまで取得し、raw label `NUMERICAL_GATE_PASSED_NOT_OVERALL`は原因推定なしで保持する。合格済みsource＋native candidateは受理対象、manual `case-6294a0a9e02c`はretry／reset／代替caseを行わず、manual 8記録が未完了のためMVP全体完了は宣言しない。V2は変更しない。

### 次に進める順序

1. 56e122b最終標準full-suite PASS（1798 passed／0 failed／3493.86 s）を受け、native-enabled final automated 8-stepを既定budgetで1 flow実施済み。gmsh 4.15.2環境で22 stage全exit 0、inspection1／preparation1／solver2／Studio1、pytest 1 passed／942.46 s、両run `SUCCEEDED`、必須5 numerical statuses `PASS`、baseline `COMPLETE`／candidate comparisonを記録した。manual 8はその後2026-09-22のmanual final flowで完遂し、[manual 8実行記録](../reviews/2026-09-22-mvp-manual-final.md)を正とする。同日、ユーザー承認済みのV2 docs統合をff-onlyで実施済み（pushは未実施、PM判断）。2026-09-21の旧manual `case-6294a0a9e02c`はretry／reset／代替caseを行わず履歴として保持する。automated flowと旧manual incidentは[canonical review](../reviews/2026-09-20-mvp-planar-e2e.md)を正とする。
2. 実モデルの入力、条件、および使用許可が整い次第、P7（E2E-02、E2E-03）に着手する。
3. MVP以降の目標範囲（§7 backlog）については、対応表の実測証拠が得られた項目から順次着手する。

## 4. 検証項目

標準の `python -m pytest` は `tests/unit` および `tests/component` を対象とし、外部の実モデルや資格情報は必要としない。`tests/native` と `tests/e2e` は個別の必須検証として扱う。ソフトウェア試験、実ツール試験、画面確認、実モデル試験の各証拠は明確に区別する。

| 検証ID | 確認する対象 | MVP |
|---|---|---|
| CT-01 | 物理的根拠、単位、スキーマ、対応範囲、必須値、未指定値およびゼロの扱い | ○ |
| CT-02 | 草案世代、質問の一度限りの適用、親版、登録に基づく実行権限 | ○ |
| CT-03 | 原子的保存、二重起動防止、パス・リンク境界、クラッシュからの復旧 | ○ |
| GM-01 | STEPの単位・対象ボディ、治具の寸法・姿勢 | 直方体のみ |
| GM-02 | 領域、Tet10の節点・面順、法線、品質、接触面の非共有 | ○ |
| GM-03 | 再メッシュ時の領域継承、形状変更時の再解決、キャッシュの完全性 | ○ |
| FB-01 | 材料、剛体、支持、接触、運動、出力のFEBio変換 | 線形弾性・摩擦なしのみ |
| FB-02 | 所有プロセスおよび子孫プロセスの終了、停止、タイムアウト、復旧 | ○ |
| FB-03 | XPLTのバージョン・変数・状態・符号、古い出力・途中出力・改ざん出力の拒否 | ○ |
| QA-01 | 既知解、接触参照解、必須数値品質、メッシュ依存性 | 弾性パッチと5項目のみ |
| VW-01 | Studioで対象XPLTを開いた証拠 | `LAUNCHED` |
| AI-01／AI-02 | 型付き提案・根拠・権限の検証／実LLM経由での入力、質問、および版確定 | 対象外 |
| RV-01／CP-01 | 旧版の保存・差分・再解析・予算／共通評価軸・単位・領域・集計・比較 | ヤング率変更のみ |
| PK-01 | ローカル必須検証、wheel配布、インストール先からの起動・読み込み | ○ |
| E2E-01 | インストール済みCLIによる合成STEPの解析・表示・編集・再解析・比較 | ○ |
| E2E-02 | 許可された実STEPによる同一の全経路 | MVP後 |
| E2E-03 | 最終BottomFrame実モデルによる同一の全経路および指定品質 | MVP後 |

検証対象がゼロ件である場合、必須試験を省略した場合、ツールや資格情報が不足している場合、ならびに正常終了が確認されていない実行については、合格と認めない。模擬ログ、模擬XPLT、模擬画面は、実アプリケーションの証拠としては扱わない。

障害試験は、専用の合成データおよび所有プロセスを用いて実施する。根拠の不足、古い質問や差分、同時更新、入力・出力の差し替え、読み込み中の切り詰め、子プロセスの書き込み継続、二重再開、空・誤った向き・共有節点を持つ接触面、比較条件の不一致、任意コードを含むLLM応答、および予算超過を対象とする。

## 5. 数値検証ケース

以下の数値は検証用の合成条件および事前定義した許容値であり、実部品の物性値や許容値ではない。参照計算と実測値は個別に記録するものとし、結果を確認した後に許容値を緩和することは認めない。

### 均一ひずみの弾性パッチ（MVP）

等方線形弾性の直方体に対し、軸ひずみとポアソン収縮を含む均一変位場を与える。底面の全面固定によって単軸応力の参照場を代用してはならない。

| 条件 | 値・判定 |
|---|---|
| 材料・寸法 | E＝1 MPa、ν＝0.3、L＝10 mm、A＝100 mm² |
| 圧縮量・参照力 | δ＝0.01 mm、F＝EAδ/L＝0.100 N |
| 適用・合格 | 小ひずみ、接触なし。反力および代表応力の相対誤差1%以内、変位場と符号が一致すること |

等価な単位系を用いても、同一のSI結果が得られることを確認する。

### 同一ケースの3段階メッシュと材料変更（MVPは材料変更のみ）

初期対象は、平面・線形弾性・摩擦なしの直方体治具とする。同一ケースにおいて、事前定義した粗・中・細の3サイズを順に生成・解析する。`prepare-planar --parent-revision-id` において変更するのは `global_size` のみとし、各版が固有の生成根拠を保持する。4回目は細メッシュ版のヤング率のみを明示的に変更し、メッシュを再生成することなく既存メッシュを再利用する。

実行回数の上限はメッシュ生成3回・FEBio実行4回とし、失敗した試行もこの上限を消費する。治具の反力には全保存状態の曲線を用い、最大値のみへの縮約は行わない。MVPでは1サイズ＋ヤング率変更（メッシュ生成1回・FEBio実行2回）をもって完了とし、3段階メッシュはメッシュ依存性を必須要件へ戻す際の検証項目とする。

`FEBIO_CAE_E2E_SETTINGS` の `preparation_requests` に要求のパスとコンテンツハッシュを、`limits` に生成・解析回数を記録し、`qualification_bundle` は省略可能とする（省略時は組み込み既定の対応表を適用）。必要な出力は、部品および治具それぞれの全体変位、治具全体の反力・絶対位置、ならびに明示固定領域の支持反力である。各出力は個別のIDで保持する。

## 6. 必須ローカル検証と配布

実装作業中は影響を受ける試験および静的検査を活用し、修正が収束した最終候補に対して以下を一括して実行する。

```powershell
python -m pytest
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests
python scripts/scan_cae_data.py --root .
python -m build
```

試験ごとに未使用の `--basetemp` を割り当て、対象コミット、作業ディレクトリ、Python実行環境、有効な引数、未コミットの差分、開始・終了時刻、終了コード、件数、およびログを記録する。テスト収集や環境起因の失敗を、製品動作の不具合再現と混同してはならない。

CAE境界スキャナーにより追跡対象ファイルとコミット候補を検査し、実CAEデータ、資格情報、デスクトップ状態、および `02_CAE` の混入を検知・拒否する。

ビルドログから該当するwheelを一意に特定し、ファイル名およびSHA-256を記録する。これをPython 3.12の新規仮想環境へ通常インストールし、ソース外の新たな作業ディレクトリにて `PYTHONPATH` の影響を排除したうえで起動する。バージョン表示およびインストール先からのモジュール読み込みを確認し、E2E試験は同一のインストール済みCLIを用いて実行する。

## 7. 実ツール・実モデルの受け入れ

試験名、マーカー、環境設定の存在、および収集対象を確認したうえで、以下を実行のエントリポイントとする。

```powershell
python -m pytest tests/e2e/test_installed_synthetic.py
python -m pytest tests/native -m "febio"
python -m pytest tests/native -m "llm"
python -m pytest tests/e2e/test_authorized_real_case.py
python -m pytest tests/e2e/test_bottomframe_final.py
```

各実行には固有の一時領域と記録先を割り当てる。ケース、実行ファイル、許可領域、条件、評価基準、予算は環境から型付き設定へと解決し、機密情報や実ケースのパスをGitに記録してはならない。

E2E-02およびE2E-03は、対象CAD、ケース領域、許可された操作、物理条件、および評価基準が確定した後に実施する。実 `02_CAE` への書き込みはこの最終工程における新規実行領域のみに限定し、元データを確実に保持する。実モデルごとに、STEP調査 → 条件確定 → 版固定 → 剛体・メッシュ生成 → 実FEBio → 必須品質 → Studio表示 → 1項目以上の編集 → 新規解析 → 比較の一連のプロセスを完遂する。BottomFrameの正式な入力や条件が揃っていない場合は、E2E-03を未実施として残す。

## 8. MVP後の目標範囲（backlog）

対応表の実測証拠が得られた項目から順次着手する。各項目に対して小規模な合成検証ケースを用意し、釣り合い、食い込み、およびメッシュ依存性の判定基準をあらかじめ確定しておく。

| 項目 | 検証ケース |
|---|---|
| メッシュ依存性の必須化 | §5の3段階メッシュ。登録済み3結果の連続比較、および変更後の弾性率で正規化した反力の比較 |
| 球治具・ヘルツ接触 | 摩擦・接着・重力なしの条件における剛体球と線形弾性半空間。`E* = E/(1−ν²)`、`F = (4/3)E*√R δ^(3/2)`（[MIT講義資料 式73](https://ocw.mit.edu/courses/20-310j-molecular-cellular-and-tissue-biomechanics-spring-2015/2910b668e59306bcf9ba5046b51215e5_MIT20_310JS15_Kamm2.2.pdf)）。E＝1 MPa、ν＝0.3、R＝10 mm、δ＝0.01 mm における参照力は約0.00463 N。最終反力の参照誤差5%以内、試験片拡大時の反力差1%以内、接触部を連続2回細分化した際の反力差がそれぞれ2%以内であることを確認する。なお、δは接触開始後の押し込み深さとする |
| 円柱治具 | 単純変形における解析値との照合 |
| ソースローカル細分化 | `source_local_mesh_dependence`：`global_size` を固定し、明示した局所球の内部で3段階の局所サイズを適用する。局所辺数の増加・最大辺長の減少・要素数の増加、および力履歴の差異によって判定する（実装ノート §11） |
| 符号校正の再試行 | calibration05 は不合格のまま保持する。運動スケールで区間を補正した新規プロトコルを凍結し、独立レビュー後に1回限り実行する（実装ノート §11） |
| 曲面接触の干渉判定 | Tri6面と球・円柱・直方体の符号付き最小距離の区間包囲。閾値が区間と交差する場合は `UNVERIFIED` とする（実装ノート §11） |
| 有限摩擦 | 正の接触圧下における固着・滑り挙動とCoulomb限界の検証 |
| 圧縮性Neo-Hookean | 単純変形における解析値との照合（P0-Bにおける単独観測結果は `docs/reviews/archive/2026-09-07-p0-b-neo-hookean-observation.md`） |
| Studio `CONFIRMED` | 実装ノート §7 に定める観測プロトコルを実行する公開ヘルパー |
| 日本語入力・LLM | `intent/answer/edit` を実LLM経由で正常に完了させる（AI-02）。受理文法の拡張については、「推測禁止」の原則と整合させつつ別途決定する |
| 拡張 ST-01 | 制約付きベイズ最適化。設計変数、CAD編集レシピ、目的、制約、および予算を明示し、既存の解析・品質・比較契約を再利用する |
