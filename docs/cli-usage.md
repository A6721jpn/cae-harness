# CLI使用ガイド

Python 3.12のheadlessプロトタイプ、`febio-cae 0.1.0`の公開コマンドを説明する。製品仕様の決定元は[設計仕様書](specs/2026-08-27-febio-llm-cae-harness-design-v2.md)と[実装・検証計画](plans/2026-08-27-febio-cae-harness-greenfield-plan.md)。本書は仕様を追加しない。

## インストール済みCLIの確認

```powershell
febio-cae --version
febio-cae --help
febio-cae case --help
febio-cae compare --help
febio-cae doctor --help
```

`febio-cae doctor --json`はネイティブツールの検出情報を返す。`FOUND_UNVERIFIED`は実行ファイルの発見であり、互換性認定や解析成功ではない。

以下は構文を示すパラメーター化例であり、実行済みのデモ手順ではない。引用符内の`<...>`を、操作者が確認したパス・登録ID・値に置き換える。`<GENERATION>`と`<WINDOW_ID>`は整数、`<TIMEOUT_SECONDS>`は秒数に置換する。IDや世代は実際の応答から取得し、値を推測しない。状態ディレクトリは同一ケースの全操作で揃える。

実CAEモデル・結果はGitに入れない。実データの`02_CAE`はツールリポジトリ外であり、最終承認済みE2Eまでは変更しない。以下の書き込み・実行操作は対象ケースへの承認がある場合に限る。

## 登録、明示的な条件設定、固定

`case`の`--state-dir`はアクション名より前に置く。

```powershell
febio-cae case --state-dir '<STATE_DIR>' create --case-root '<CASE_ROOT>' --cad '<CAD_PATH>' --json
febio-cae case --state-dir '<STATE_DIR>' inspect '<CASE_ID>' --json
febio-cae case --state-dir '<STATE_DIR>' spec '<CASE_ID>' --file '<SPEC_JSON>' --expected-generation '<GENERATION>' --json
febio-cae case --state-dir '<STATE_DIR>' validate '<CASE_ID>' --json
febio-cae case --state-dir '<STATE_DIR>' freeze '<CASE_ID>' --json
```

`create`はCADソースとケースの登録であり、物理条件の自動決定や実行可能メッシュの準備ではない。`inspect`の終了コード0もメタデータ照会の成功を示すだけで、ネイティブ形状検査の成功とは限らない。

`spec`はUTF-8 JSONの明示的な型付き要求を受け取る。ラッパーには`schema_version`（文字列`"1"`）、`values`（`PartialCaseSpec`）、`evidence`、`source_declarations`が必要で、`input_intent`は任意。正確な定義は[要求パーサー](../src/febio_cae/application/specs.py)と[共通コーデック](../src/febio_cae/domain/codec.py)を参照する。自然言語だけで必要な物理条件を補完するインターフェースではない。材料・荷重・拘束・接触・ROIを形状や慣例から推測せず、根拠を指定する。

`--expected-generation`には現在のドラフト世代を渡す。更新後は返却された新しい世代を使う。`validate`で必須条件が不足して`NEEDS_INPUT`となった場合は根拠を補い、再検証する。`freeze`は検証済み条件を不変のリビジョンにするが、ソルバー実行を意味しない。

部分編集には親に結び付いた型付き`CasePatch`を渡す。自由形式のJSON差分ではない。変更後も検証・固定が必要となる。

```powershell
febio-cae case --state-dir '<STATE_DIR>' patch '<CASE_ID>' --file '<PATCH_JSON>' --expected-generation '<GENERATION>' --json
```

## 登録済み平面プロトタイプの実行

`run-demo`は制限付きの内部登録経路である。対象リビジョンの平面デモ登録、採用済みメッシュと来歴・検査記録、互換性プロファイル、登録されたリーダー資産、予算などの前提を必要とし、ソルバーとリーダーの一致も確認する。この準備を任意のSTEPに対して行う汎用の公開CLIはない。`create`→`spec`→`freeze`だけで任意の部品を実行できるとは限らない。前提がない場合、記録を手作業で偽装せず未対応として扱う。

```powershell
febio-cae case --state-dir '<STATE_DIR>' run-demo '<CASE_ID>' --revision-id '<REVISION_ID>' --solver '<SOLVER_EXE>' --preflight --json
febio-cae case --state-dir '<STATE_DIR>' run-demo '<CASE_ID>' --revision-id '<REVISION_ID>' --solver '<SOLVER_EXE>' --json
```

`--preflight`はコンパイルのみでネイティブプロセスを起動しないが、登録前提を回避するオプションではない。`PREFLIGHT_PASSED`は解析成功ではない。実行後の`NEEDS_PREVIEW`も終了コード0となるが、タスク完了ではない。`NEEDS_REVIEW`は品質確認が必要な状態として扱う。

## 既存Studioセッションのプレビュー観測

公開`preview`はStudioを起動せず、指定された既存セッションを観測する。登録された出力manifest、実際のウィンドウID、Studio実行ファイル、タイムアウトを明示する。単に画面が開いているだけではプレビュー証跡の成立を意味しない。

```powershell
febio-cae case --state-dir '<STATE_DIR>' preview '<CASE_ID>' --manifest-id '<MANIFEST_ID>' --window-id '<WINDOW_ID>' --studio '<STUDIO_EXE>' --timeout '<TIMEOUT_SECONDS>' --json
febio-cae case --state-dir '<STATE_DIR>' preview-status '<CASE_ID>' --preview-id '<PREVIEW_ID>' --json
```

`preview-status`は保存済み証跡を再検証する。照会の終了コード0と`task_status=COMPLETE`を区別する。公開コマンドに`preview --open`はない。

## 限定された材料編集比較

```powershell
febio-cae compare '<BASELINE_RUN_ID>' '<CANDIDATE_RUN_ID>' --case-id '<CASE_ID>' --state-dir '<STATE_DIR>' --spec '<COMPARISON_SPEC_JSON>' --json
```

登録済み平面ケースの直接の材料編集子リビジョンが対象で、意図した変更は`material.youngs_modulus`のみ。`ComparisonSpec`は明示的に指定し、その他の固定条件、実際の治具・部品body ID、共通の観測区間を揃える。必要な固定条件一覧と軸の識別子は`febio-cae compare --help`に表示される。任意のrunや自由なROIを比較できる機能ではない。

支持する量は治具全体の符号付きWorld-z接触力（N）と、部品全節点のWorld-z変位絶対値の最大値（m）。横軸は治具のWorld-z位置変化の負値（m）で、接触開始からの深さではない。保存状態ごとに集約し、共通観測区間内の両者のサンプル位置の和集合へ線形補間する。外挿は行わない。差はcandidate−baseline、相対差はbaseline基準で、baselineが0の場合は未定義となる。結果は精度保証、材料妥当性、優劣ランキングを示さない。

## 状態照会と制限付き中断処理

```powershell
febio-cae status '<RUN_ID>' --case-id '<CASE_ID>' --state-dir '<STATE_DIR>' --json
febio-cae resume '<RUN_ID>' --case-id '<CASE_ID>' --state-dir '<STATE_DIR>' --json
febio-cae cancel '<RUN_ID>' --case-id '<CASE_ID>' --state-dir '<STATE_DIR>' --json
```

`status`は読み取り専用。正常に照会できれば終了コード0であり、runが成功したという意味ではない。`run_status`、`quality_status`、`preview_status`、`task_status`と診断を確認する。

`resume`はソルバーの再起動や一般的な解析再開ではない。プロセスを持たない同期runの`VALIDATING`状態について、writerが閉じていて検証済みmanifestが1件ある等の条件を確認し、回復用leaseを取得して中断状態を確定する限定操作である。`INTERRUPTED`の終了コードは7。

`cancel`は準備前の同期`CREATED` runに限り、ネイティブプロセスも出力もないことを確認して`CANCELLED`にする。タスクは`INTERRUPTED`、終了コードは7となり、同じキャンセルの再要求は冪等である。実行中ソルバーを停止する機能ではない。ネイティブ実行文脈は未対応（4）、lease競合は競合（8）として扱い、他の所有者の状態を上書きしない。これら限定状態を作るための汎用公開準備コマンドはない。

終了コードの基本分類は0＝操作成功、2＝入力不正、3＝必須条件待ち、4＝環境・機能未対応、5＝実行失敗、6＝整合性・品質問題、7＝中止・中断、8＝競合。各コマンドのJSON状態・診断を併せて読む。すべてのコマンドがすべての分類を返すわけではない。

## 検証範囲

本ガイドの構文は現行パーサーとインストール済みCLIのhelp/versionで確認した。パラメーター化例をケースに対して実行した証拠ではない。

固定した合成ケース経路では実FEBio・Studioを使用した検証がある。一方、表面近似は`UNVERIFIED`であり、FB03の独立したformat 3検証も未完了。広範なネイティブ互換性・プロファイル、科学的妥当性・材料妥当性、一般的な再開、live LLM経路、実モデルおよび最終BottomFrame E2Eの成功は主張しない。合成テストの成功を実モデル成功に読み替えない。本書の追加だけでP6やプロジェクト全体が完了するわけではない。
