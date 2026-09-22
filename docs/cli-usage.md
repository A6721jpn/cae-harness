# CLI使用ガイド

Python 3.12のheadless CLI、`febio-cae 0.1.0`の公開コマンドを説明する。製品仕様の決定元は[設計仕様書](specs/2026-09-14-febio-llm-cae-harness-design-v2.md)と[実装・検証計画](plans/2026-09-14-febio-cae-harness-greenfield-plan.md)、細部は[実装ノート](specs/implementation-notes.md)。本書は仕様を追加しない。MVPの8手順は計画書 §2、実行証拠は検証記録を参照する。

開発・修正の担当と判断権限は[開発契約](../AGENTS.md)に従う。Orca・Codexなどの開発用ツールは製品の実行時依存ではない。

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

## コード変更後のwheel・新規環境・設定identityの更新

コードを変更した候補は、既存の仮想環境やソースツリーからのimportだけで受理済みとしない。毎回wheelを作り、新規環境へ通常インストールし、設定テンプレートの`installed_python`と`wheel`だけを同じ候補の実体へ更新する。以下は共有設定を変更せず、別ファイルへBOMなしUTF-8で書き出すPowerShell 5.1+の例である。テンプレート内の要求・物理条件・比較軸・limits・準備要求の値は変更せず保持する。identityが`null`の未束縛テンプレートにも対応する。

```powershell
python -m build --wheel --outdir '<WHEEL_DIR>'
$wheel = Get-Item -LiteralPath (Join-Path '<WHEEL_DIR>' 'febio_cae-0.1.0-py3-none-any.whl')
python -m venv '<FRESH_ENV>'
& '<FRESH_ENV>\Scripts\python.exe' -m pip --disable-pip-version-check install "$($wheel.FullName)[native]"

$sourceFree = '<SOURCE_FREE_DIR>'
New-Item -ItemType Directory -Force -Path $sourceFree | Out-Null
Push-Location $sourceFree
try {
    & '<FRESH_ENV>\Scripts\python.exe' -I -c "import febio_cae, importlib.metadata, json, sys; print(json.dumps({'executable': sys.executable, 'module': febio_cae.__file__, 'version': importlib.metadata.version('febio-cae')}))"
    & '<FRESH_ENV>\Scripts\python.exe' -I -m febio_cae --version
    & '<FRESH_ENV>\Scripts\python.exe' -I -m febio_cae --help
    & '<FRESH_ENV>\Scripts\python.exe' -I -m febio_cae case --help
}
finally {
    Pop-Location
}

$python = Get-Item -LiteralPath '<FRESH_ENV>\Scripts\python.exe'
$pythonHash = (Get-FileHash -LiteralPath $python.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$wheelHash = (Get-FileHash -LiteralPath $wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$settings = Get-Content -LiteralPath '<SETTINGS_TEMPLATE>' -Raw | ConvertFrom-Json
$settings.installed_python = [ordered]@{
    path = $python.FullName
    size = [int64]$python.Length
    sha256 = $pythonHash
}
$settings.wheel = [ordered]@{
    path = $wheel.FullName
    size = [int64]$wheel.Length
    sha256 = $wheelHash
}
$json = $settings | ConvertTo-Json -Depth 100
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText('<UPDATED_SETTINGS>', $json + [Environment]::NewLine, $utf8NoBom)
```

`installed_python`と`wheel`のキーはそれぞれ`path`、`size`、`sha256`であり、サイズは`Get-Item.Length`、SHA-256は小文字で記録する。`$wheel.FullName`をinstallと設定更新の両方に使うため、実際に保持したwheelの正確なパスを記録できる。`<UPDATED_SETTINGS>`を新規に作成するので、元テンプレートや共有設定は変更しない。JSONを書き出した後は先頭3バイトがUTF-8 BOM（`EF BB BF`）でないこと、`installed_python`／`wheel`以外の全キーとネスト値がテンプレートと一致することを確認し、この同じ設定をE2Eへ渡す。

## MVPの8手順

新規環境へ通常インストールしたwheelのCLIを使う。STEP、明示条件の `SpecUpdateRequest`、ヤング率だけを変更する `CasePatch`、および `ComparisonSpec` は事前に用意する。物理条件・選択集合は操作者の根拠に結び付け、検査で取得したbody IDとdigestを使用する。以下は構文例であり、合格の実行記録ではない。

```powershell
# 1. 登録とネイティブ形状検査。返却されたCASE_IDを以後に使用する。
febio-cae case --state-dir '<STATE_DIR>' create --case-root '<CASE_ROOT>' --cad '<SYNTHETIC_STEP>' --json
febio-cae case --state-dir '<STATE_DIR>' inspect '<CASE_ID>' --native --wall-seconds 600 --cpu-workers 1 --json

# 2. 組み込み対応表を登録する。外部bundleは指定しない。
febio-cae case --state-dir '<STATE_DIR>' provision-planar-profiles '<CASE_ID>' --json

# 3. 明示条件を登録する。まだメッシュ準備完了とは扱わない。
febio-cae case --state-dir '<STATE_DIR>' spec '<CASE_ID>' --file '<SPEC_JSON>' --expected-generation '<CURRENT_GENERATION>' --json

# 4. 準備後にREADYを確認する。freezeのIDはPREPAREDのIDと一致する。
febio-cae case --state-dir '<STATE_DIR>' prepare-planar '<CASE_ID>' --file '<PREPARATION_JSON>' --expected-generation '<SPEC_GENERATION>' --json
febio-cae case --state-dir '<STATE_DIR>' validate '<CASE_ID>' --json
febio-cae case --state-dir '<STATE_DIR>' freeze '<CASE_ID>' --json

# 5. 準備済みの不変版を実FEBioで実行する。
febio-cae case --state-dir '<STATE_DIR>' run '<CASE_ID>' --revision-id '<PREPARED_REVISION_ID>' --solver '<SOLVER_EXE>' --json

# 6. run、品質、preview、taskの状態を別々に確認する。
febio-cae status '<BASELINE_RUN_ID>' --case-id '<CASE_ID>' --state-dir '<STATE_DIR>' --json

# 7. baselineのmanifestに記録されたXPLTをStudioで開く。
febio-cae case --state-dir '<STATE_DIR>' preview '<CASE_ID>' --manifest-id '<BASELINE_MANIFEST_ID>' --studio '<STUDIO_EXE>' --json
febio-cae case --state-dir '<STATE_DIR>' preview-status '<CASE_ID>' --preview-id '<BASELINE_PREVIEW_ID>' --json

# 8. E-only変更根拠を登録し、応答の根拠参照をCasePatchへ結び付ける。
febio-cae case --state-dir '<STATE_DIR>' spec '<CASE_ID>' --file '<E_ONLY_EVIDENCE_JSON>' --expected-generation '<CURRENT_GENERATION>' --json
febio-cae case --state-dir '<STATE_DIR>' patch '<CASE_ID>' --file '<E_ONLY_PATCH_JSON>' --expected-generation '<E_EVIDENCE_GENERATION>' --json
febio-cae case --state-dir '<STATE_DIR>' validate '<CASE_ID>' --json
febio-cae case --state-dir '<STATE_DIR>' freeze '<CASE_ID>' --json
febio-cae case --state-dir '<STATE_DIR>' run '<CASE_ID>' --revision-id '<CHILD_REVISION_ID>' --solver '<SOLVER_EXE>' --json
febio-cae compare '<BASELINE_RUN_ID>' '<CANDIDATE_RUN_ID>' --case-id '<CASE_ID>' --state-dir '<STATE_DIR>' --spec '<COMPARISON_SPEC_JSON>' --json
```

手順6では実行の`run_status=SUCCEEDED`に加え、必須5項目の`PASS`と`quality_status=PASS`を確認する。MVP既定の全体メッシュ依存性だけ`UNVERIFIED`を許容し、明示されたsource-local要件は免除しない。既知の失敗は無視しない。手順7の`LAUNCHED`は起動証拠であり、表示内容を独立観測した`CONFIRMED`ではない。必須品質を満たしたbaselineの`task_status=COMPLETE`と、未表示のcandidateの`NEEDS_PREVIEW`を混同しない。

`E_ONLY_EVIDENCE_JSON` は既存条件を変えず、変更指示の `source_declarations` を登録する要求である。その応答で得た根拠参照をpatchのヤング率根拠へ結び付ける。patchには親版IDと親spec digestを指定し、ヤング率以外の物理条件は保持する。登録後の世代と根拠参照を推測・捏造しない。

MVP用E2Eの1試行では、準備1回・ソルバー2回・baselineのStudio起動1回、および別枠の形状検査1回を上限とする。失敗した要求もその試行の実績として記録する。この試験内上限を開発全体の累積上限に読み替えない。不具合修正後の許可済み合成入力の環境再検査は通常の開発検証であり、ケースに登録した解析予算の拡張や回避とは区別する。`tests/e2e/test_installed_synthetic.py` の1-request設定はこの経路を検証し、3-request設定は別予算のメッシュ依存性調査を維持する。テスト設定には実際にインストールしたPython、wheel、STEP、ソルバー、Studio、準備要求のパス・サイズ・SHA256を結び付ける。

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

## 公開平面経路（STEP調査、対応表登録、平面準備）

`run` が要求する準備済み版を新規ケースで作成する公開入口は次の3つである。初回は調査・対応表登録・型付き条件登録・準備の順に進める。

```powershell
febio-cae case --state-dir '<STATE_DIR>' inspect '<CASE_ID>' --native --wall-seconds '<SECONDS>' --cpu-workers '<N>' --json
febio-cae case --state-dir '<STATE_DIR>' provision-planar-profiles '<CASE_ID>' --json
febio-cae case --state-dir '<STATE_DIR>' prepare-planar '<CASE_ID>' --file '<REQUEST_JSON>' --expected-generation '<GENERATION>' --json
febio-cae case --state-dir '<STATE_DIR>' prepare-planar '<CASE_ID>' --file '<REQUEST_JSON>' --expected-generation '<GENERATION>' --parent-revision-id '<PARENT_REVISION_ID>' --json
```

`inspect --native`は登録済みSTEPを所有子プロセスで1回調査し、ボディ、閉じたソリッド、単位、体積、面、欠陥を返す。物理条件や選択済みボディを要求せず、材料・支持・接触・評価領域の意味を付与しない。`INSPECTED`は観測完了であり、`native_qualification`は`UNVERIFIED`のまま。`--wall-seconds`は正の有限値で上限600秒、`--cpu-workers`は利用可能数以下の正整数。

`provision-planar-profiles`は、製品組み込みの既定対応表（ソルバー・出力・品質の3プロファイルとメッシュ品質基準）を新規ケースへ登録する。`--bundle-path`で外部バンドルを指定した場合は、その構造・証拠・対象範囲を検証してから登録する。`PROVISIONED`は登録完了を示し、実ツール操作数は0で、解析や品質の合格ではない。同じIDで内容が異なる対応表は競合として拒否される。

`prepare-planar`は`spec`と同じ`SpecUpdateRequest`形式の要求を受け取り、平面・直方体治具・`AsPlaced`配置の範囲でメッシュ生成と準備記録の公開を行う。初期上限は600秒、生成1回、四面体100,000要素、250,000節点。`--parent-revision-id`を指定すると、登録済み準備完了親の`global_size`だけを変えた細分化版を作る。ケース全体でメッシュ3回・FEBio4回の予約があり、失敗・中断でも消費は戻らない。`PREPARED`は準備記録の公開であり、解析の実行や合格ではない。

細分化（`--parent-revision-id`）では現在の固定リビジョンを親として指定し、未固定の変更を残さない。要求の`mesh_policy.quality_profile`には元の生成プロファイルを指定する。

- `mesh_dependence`では、宣言した3段階の隣接する次の`global_size`へ進める。
- `source_local_mesh_dependence`では、`global_size`、選択領域、source-local ballの位置・半径を保ち、宣言した局所サイズの隣接する次の段階へ進める（MVP後の範囲）。
- 材料、運動、支持、接触、出力、品質条件、予算を同時に変更しない。段階の飛び越しや細分化回数超過は拒否される。要求形式は[preparation要求パーサー](../src/febio_cae/application/_preparation_request.py)を参照する。

## 日本語入力（LLM接続）

```powershell
febio-cae case --state-dir '<STATE_DIR>' intent '<CASE_ID>' --text '<TEXT>' --expected-generation '<GENERATION>' --operation-id '<OPERATION_ID>' --llm-settings '<SETTINGS_JSON>' --json
febio-cae case --state-dir '<STATE_DIR>' answer '<CASE_ID>' --question '<QUESTION_ID>' --text '<TEXT>' --expected-generation '<GENERATION>' --operation-id '<OPERATION_ID>' --llm-settings '<SETTINGS_JSON>' --json
febio-cae case --state-dir '<STATE_DIR>' edit '<CASE_ID>' --base '<REVISION_ID>' --text '<TEXT>' --expected-generation '<GENERATION>' --operation-id '<OPERATION_ID>' --llm-settings '<SETTINGS_JSON>' --json
```

受理する文は一行全体の`field = value`又は`field: value`に限る。対象は材料モデル、ヤング率、ポアソン比、ひずみ・速度適用性と、`support = adopt <revision-id>.support`のような登録済み構成要素の明示採用。否定・仮定・曖昧文・単なる数値の出現は条件にしない。`edit`は等方線形弾性のヤング率置換だけを受け付ける。意図・回答から自動凍結・自動解析はしない。`--llm-settings`は`provider, model, key_env, budget, input_tokens, output_tokens, socket_seconds`を持つJSONで、鍵は環境変数名で渡し、値を引数に書かない。

## 準備済み平面モデルの実行

`run` は `prepare-planar` が生成した登録済みの不変版を実行する。採用済みメッシュと生成・検査記録、互換性プロファイル、登録された読込器、予算、およびソルバーの実体を照合する。`create → spec → freeze` だけではメッシュは準備されない。初回の `prepare-planar` は形状・選択集合・メッシュを結び付けた版を確定するため、準備後に `validate` と `freeze` で `READY` と同じ不変版を確認する。旧公開コマンド `run-demo` は使用しない。

```powershell
febio-cae case --state-dir '<STATE_DIR>' run '<CASE_ID>' --revision-id '<REVISION_ID>' --solver '<SOLVER_EXE>' --preflight --json
febio-cae case --state-dir '<STATE_DIR>' run '<CASE_ID>' --revision-id '<REVISION_ID>' --solver '<SOLVER_EXE>' --json
```

`--preflight`はコンパイルのみでネイティブプロセスを起動しないが、登録前提を回避するオプションではない。`PREFLIGHT_PASSED`は解析成功ではない。実行後の`NEEDS_PREVIEW`も終了コード0となるが、タスク完了ではない。`NEEDS_REVIEW`は品質確認が必要な状態として扱う。

## Studioの起動（MVP）

```powershell
febio-cae case --state-dir '<STATE_DIR>' preview '<CASE_ID>' --manifest-id '<MANIFEST_ID>' --studio '<STUDIO_EXE>' --json
febio-cae case --state-dir '<STATE_DIR>' preview-status '<CASE_ID>' --preview-id '<PREVIEW_ID>' --json
```

`preview` は現在のXPLTの実体とハッシュを検証して、Studioへファイルを引き渡す。起動記録は `LAUNCHED`、実行が `SUCCEEDED` かつ必須品質が合格なら `task_status=COMPLETE` となる。MVPではメッシュ依存性だけの `UNVERIFIED` を許容するが、他の5項目は `PASS` が必要であり、既知の `FAIL` は許容しない。

記録には実行ファイルのパス・ハッシュ、PID、起動時刻、取得できた版を保存する。版情報がない場合は理由付き `UNVERIFIED` とし、数字を推測しない。`LAUNCHED` は起動とファイル引き渡しの証拠であり、表示内容を独立確認した `CONFIRMED` ではない。材料変更後の子版は別の結果であり、親版の起動記録で表示済みとは扱わない。

## 既存Studioセッションの独立観測（MVP後）

`--window-id` を指定した `preview` はStudioを起動せず、指定された既存セッションに対する外部の独立した操作者・観測側の証跡を受け付ける。登録された出力manifest、実際のウィンドウID、Studio実行ファイルに加え、Windowsのリダイレクトされた標準入力PIPEで観測応答を返す仕組みが必要となる。端末またはファイルを標準入力にした観測モードの実行は拒否される。タイムアウトは有限の正数で120秒以下に指定する。

観測側は標準出力の`PREVIEW_REQUESTED`を受け取り、その都度発行されたnonce、セッション、ソースファイル・digest、要求された表示状態・時刻・変数・成分・座標系・単位に結び付く新鮮な観測結果を、改行終端のUTF-8 JSON 1件として標準入力PIPEへ返す必要がある。独立した観測者の帰属情報と、要求発行後に新規取得したPNGキャプチャも必要で、有限の期限内に検証される。静的な確認JSONや過去のキャプチャの再利用では成立しない。正確な通信仕様は[CLI観測プロトコル](../src/febio_cae/cli/preview.py)、応答の照合・キャプチャ要件は[観測検証処理](../src/febio_cae/application/_preview.py)を参照する。

この独立観測を行う公開のエンドユーザー向けヘルパーは現行CLIにはない。下の `--window-id` 付き行は観測モードの構文を示す。CLI自体が独立した観測やPNG取得を代行するわけではなく、画面が開いているだけでは `CONFIRMED` の証跡は成立しない。

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

### 証拠rootと受け入れ境界

| 記号 | 実体 |
|---|---|
| `<COORDINATION>` | 元のチェックアウト/.local/coordination |
| `<ROOT_PYTEST>` | 元のチェックアウト/.local/pytest-basetemp |
| `<WORKTREE_PROOFS>` | Orca開発ワークツリー/.local（docs-proofのみ） |

| 対象 | 記録・状態 |
|---|---|
| identity smoke | 未束縛templateのidentity置換、BOMなしUTF-8、ソース外`-I` import、公開`--version`／`--help`をexit 0で確認。証拠は`<WORKTREE_PROOFS>/docs-proof-20260921-null-template/evidence.json`、bound templateは`<WORKTREE_PROOFS>/docs-proof-20260921-acceptance-docs/evidence.json`。build・native・solver・Studioはこのsmokeでは未実行 |
| current acceptance status | 現行candidate source=`71c388f`／test=`56e122b`／format=`0e35ba4`、native wheel SHA-256=`f978de...`／size=`404193`。native-enabled final `case-80e5f42a5043`はgmsh 4.15.2、22 stage全exit 0、pytest 1 passed／942.46 s、両run `SUCCEEDED`、必須5 numerical statuses `PASS`、baseline `COMPLETE`／Studio `LAUNCHED`／candidate comparison。比較はforce relative differences `[1,1,1,1,1,1]`、displacement differences／relative differences `[0,0,0,0,0,0]`。manual preparationは`FAILED`／`ABORTED/BLOCKED`、MVP全体完了は未宣言。[canonical review](reviews/2026-09-20-mvp-planar-e2e.md) |
| canonical evidence | 現行22-commandの実argvは`<ROOT_PYTEST>/mvp-20260921-final-auto-native-0e35ba4/test_installed_synthetic_cli_f0/installed-synthetic-attempt-report.json`の`commands[].argv`、stage／status／exitは`<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/final-accounting.json`、wrapper argvは同ディレクトリ`launch-record.json`。static／build／focused pytest、変更ファイル一覧、manual incident、known countsは[検証記録](reviews/2026-09-20-mvp-planar-e2e.md)に集約 |
| executable boundary | 8手順の実行例、settings identity binding、`LAUNCHED`／既存実装の`CONFIRMED`境界は本書前半と[設計仕様書](specs/2026-09-14-febio-llm-cae-harness-design-v2.md)を正とする。Studio画面タイトル3.1.0とreceipt version `UNVERIFIED`を区別する |
| 残件 | native-enabled final flowの数値gate・baseline `COMPLETE`・Studio `LAUNCHED`・candidate comparisonは取得済み。合格済みsource＋native candidateは受理対象として記録し、MVPで残る必須記録だったmanual 8は2026-09-22のmanual final flowで完遂した（新規case、公開CLI手動22コマンド全exit 0、inspection1／preparation1／solver2／Studio1、[manual 8実行記録](reviews/2026-09-22-mvp-manual-final.md)）。ユーザー承認済みのV2 docs統合は2026-09-22にff-onlyで実施済み（pushは未実施、PM判断）。2026-09-21の旧manual `case-6294a0a9e02c`はretry／reset／代替caseを行わず履歴として保持する。完了済み新wheel smokeと各fresh flow recordを分けて保持する。[canonical review](reviews/2026-09-20-mvp-planar-e2e.md) |

raw logsと旧FAILED reportはGit管理外で保存し、251版の自動T5（履歴）をmanual 8またはMVP全体合格へ読み替えない。パス表記はroot記号を混同しない。
