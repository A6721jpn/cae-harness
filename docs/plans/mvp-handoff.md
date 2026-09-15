# MVP実装の引き継ぎ（実装担当LLM向け）

作成日：2026-09-15。参照順序：[開発契約](../../AGENTS.md) → 本書 → [計画書 §2](2026-09-14-febio-cae-harness-greenfield-plan.md) → 必要箇所のみ[設計仕様書](../specs/2026-09-14-febio-llm-cae-harness-design-v2.md)および[実装ノート](../specs/implementation-notes.md)。

本書は計画書のタスクT1〜T6について、対象ファイルと具体的な変更箇所まで落とし込んだ作業指示書である。設計判断は計画書および仕様書を正とし、本書の記述と相違がある場合はそちらに従う。

## 1. 作業場所と環境

| 項目 | 値 |
|---|---|
| ブランチ | `orca/acceptance-integration`（最新。`V2` は約100コミット古い） |
| 作業ツリー | `C:\Users\backo\orca\workspaces\CAE-HARNESS-V2\acceptance-integration` |
| 基準コミット | `b5ab5bc`（文書再構成）。その親である `e696fbcc` がコードの最新 |
| Python | 3.12.10。`.venv/`（作成済み、Git管理外）に `pip install -e ".[dev,native]"` 済み |
| FEBio 4.12.0 | `C:\Program Files\FEBioStudio\bin\febio4.exe`（SHA-256 `03b9db12…770c9`） |
| FEBio Studio | `C:\Program Files\FEBioStudio\bin\FEBioStudio.exe` |
| Gmsh 4.15.2 | `.venv` に pip で導入済み（`import gmsh`、`.venv\Scripts\gmsh.bat`） |
| 環境変数 | `.env.example` を参照。`FEBIO_CAE_FEBIO_PATH` / `FEBIO_CAE_STUDIO_PATH` / `FEBIO_CAE_GMSH_PATH` |

```powershell
cd C:\Users\backo\orca\workspaces\CAE-HARNESS-V2\acceptance-integration
.\.venv\Scripts\Activate.ps1
$env:FEBIO_CAE_FEBIO_PATH = "C:\Program Files\FEBioStudio\bin\febio4.exe"
$env:FEBIO_CAE_STUDIO_PATH = "C:\Program Files\FEBioStudio\bin\FEBioStudio.exe"
$env:FEBIO_CAE_GMSH_PATH = "$PWD\.venv\Scripts\gmsh.bat"
febio-cae doctor --json          # 3ツールとも FOUND_UNVERIFIED になること
python -m pytest -q              # 標準試験（unit + component）
python -m ruff format --check . ; python -m ruff check . ; python -m mypy src tests
```

合成入力（Git管理外、開発機上に配置）：

| 用途 | パス |
|---|---|
| 合成STEP（直方体） | `C:\dev\CAE-HARNESS-V2\.local\v\native-inspection-05\producer\box.step` |
| 準備要求3件（粗・中・細） | `C:\dev\CAE-HARNESS-V2\.local\v\public-input04\{coarse,refined,fine}.json` |
| E2E設定の実例 | `C:\dev\CAE-HARNESS-V2\.local\v\public-settings05\settings.json` |
| 承認バンドル（T2で不要とする） | `C:\dev\CAE-HARNESS-V2\.local\coordination\acceptance-planar-profile-approved-01.json` |

新しい合成STEPが必要な場合は `.local/synthetic/` に生成する。実CAEモデルや `02_CAE` には手を触れない。

## 2. 現状の最重要事実

**本ブランチでは MVP の手順2（対応表登録）が正常に機能しない状態にある。** `src/febio_cae/application/_profile_provisioning.py` の `_APPROVED_READER_SHA256`（`8bed227c…`）は `xplt_reader.py` ソースコードの自己ハッシュであるが、本ブランチでは同ファイルが改変されており、現在のハッシュ値は `5f058c90…` となっている。したがって `provision-planar-profiles` は「installed reader differs from the qualified reader」となり失敗し、以降の処理へ進むことができない。`run-demo` も対応表内の `profile.reader.executable_digest` を照合するため同様に失敗する。

このため **T2 を最優先で実施する**（計画書上の順序は T1→T2 であるが、T2 を先行させる）。

### 2026-09-15 時点のゲート基線（`.venv`、`e696fbcc` のコード）

| ゲート | 結果 |
|---|---|
| `python -m pytest`（unit + component、1780 件） | 1780 passed、exit 0、47分42秒（`test_required_numerical_quality.py` の細分化系15件が大半の時間を占める。実装中は `-k` で絞る） |
| `ruff format --check .` | 9ファイルが未整形（`ruff --fix` 相当で機械的に直る） |
| `ruff check .` | 14件（6件は `--fix` 可。残りは `TRY004` 等の小さな修正） |
| `mypy src tests` | 88件。80件は `adapters/geometry/_gmsh_runtime.py`、残りは `tests/component/geometry/test_gmsh_runtime_identity.py` と `test_required_numerical_quality.py`（pytest 9 の `FixtureFunctionDefinition.__wrapped__`） |

ruff・mypy の赤は本ブランチが 9/14 に「ローカルゲート未実施」で中断した時点の残りである。**T0 として最初に機械的に直す**（`ruff format .`、`ruff check --fix .`、残りの lint と mypy を最小の型注釈・例外種別の変更で解消。ロジックは変えない）。ツール版は `.venv` に固定した ruff 0.16.4 / mypy 2.3.1 / pytest 9.1.1 を使う。

## 3. タスク別の作業指示

### T0：ゲート基線の修復（最初に、機械的に）

上表の ruff / mypy を解消し、`python -m pytest` が通る状態で1コミットにする。`_gmsh_runtime.py` の mypy 80件は `ctypes` 周りの型注釈不足が大半で、`cast` と `Callable[..., Any]` の注釈で閉じる。動作を変えない。

### T2：既定対応表の組み込み（最初に着手）

目的：外部バンドルおよび自己ハッシュ固定を廃止し、新規環境において `provision-planar-profiles` を正常に通過させる。

1. `src/febio_cae/resources/planar_default_profiles.json`（新規）を作成する。内容は承認バンドルの `profiles`（solver / outputs / quality の3件、`CompatibilityProfile` の辞書表現）および `mesh_quality`（`MeshQualityRegistration`）をそのまま抽出したものとする。`source_documents`（約550KBの証拠レポート）は含めない。各プロファイル内の `evidence[].reference` は証拠レポートIDを参照しているため、`reference` を `"product-default"` に、`content_digest` を新規JSONファイル自体のSHA-256に付け替える（`EvidenceRef` は `source_kind / reference / target_field / content_digest` を要求するため、`domain/evidence.py` を参照のこと）。
2. `pyproject.toml` の `[tool.setuptools]` にパッケージデータとしてJSONを含める（`package-data = {"febio_cae" = ["resources/*.json"]}`）。wheel に同梱されることを `python -m build` → `unzip -l` で確認する。
3. `_profile_provisioning.provision_planar_profiles(service, case_id, *, bundle_path: Path | None)` とし、`None` の場合は組み込みJSONを読み込む。`_APPROVED_BUNDLE_SHA256 / _APPROVED_BUNDLE_SIZE / _APPROVED_READER_SHA256` の照合は削除する。外部バンドルを渡した際の構造検証（`_parse_bundle`）は残して差し支えない。
4. `src/febio_cae/cli/main.py:50` 付近の `provision.add_argument("--bundle-path", required=True)` を `required=False` に変更し、`cli/case.py:217` 付近の呼び出し側もそれに合わせる。
5. `run-demo`（`application/_demo.py:278` 付近）における `profile.reader.executable_digest` の照合と `registered-reader-source` の照合を削除する。同様の照合が `adapters/febio/xplt_reader.py:1055` の `_check_profile` にも存在する。リーダーの識別については `ToolIdentity` のバージョン文字列のみを残す。
6. 試験：`tests/component/febio/test_profile_provisioning.py` の `_synthetic_bundle` を「既定値で登録」する試験に置き換え、外部バンドル経由のルートは1本のみ残す。`tests/e2e/test_installed_synthetic.py:30-31,1052-1060` の `_APPROVED_BUNDLE_*` および `qualification_bundle` の必須指定を解除する。

完了条件：新規の `case create` 作成直後に `provision-planar-profiles CASE --json` が `PROVISIONED` を返し、返却される3つの `NumericalProfileRef` と `mesh_quality` が準備要求3件の参照と一致すること。

### T1：汎用 `run`

現状：`run-demo`（`application/_demo.py:run_demo`）は `PlanarDemoRegistration`（旧デモ）と `CurrentPreparationRegistration`（`prepare-planar` の出力）の双方を受け付け、後者において一貫試験が動作している。すなわち、実行処理の本体はすでに完成している。

1. `cli/main.py:107` の `run-demo` パーサーと同一の引数で `run` サブコマンドを追加し、`cli/case.py:201,298` の分岐に `"run"` を加える。`run-demo` は当面の間、エイリアスとして残す。
2. `_demo.py` の `PlanarDemoRegistration` 専用ルート（`recorded_geometry`、`_RecordedInspection`、`registered-reader-source`）は MVP では使用しない。削除して差し支えないが、`tests/component/cli/test_demo_cli.py` と `tests/component/application/test_registered_execution.py` が旧デモ登録を利用している場合は、それらを `prepare-planar` ルートの試験に置き換えるか、削除する。判断に迷う場合は残したまま `run` のみを追加する。
3. 応答の `scope` 文字列（`"registered synthetic planar demonstration; …"`）を `"planar MVP path"` 程度の内容に修正する。

完了条件：`prepare-planar` により `PREPARED` となったバージョンにおいて、`run CASE --revision-id R --solver febio4.exe --json` で実FEBioが動作し、`run_status=SUCCEEDED` となること。なお、`--preflight` は `PREFLIGHT_PASSED` とする。

### T3：メッシュ依存性の任意化

対象箇所：`src/febio_cae/application/_required_quality.py:74` の `_quality_status`。

- `required` で全行に `PASS` を要求している箇所のうち、`criterion_id` が `mesh_dependence` である行に限っては `UNVERIFIED` も許容する。`FAIL` は従来どおり全体が `FAIL` であることを求める。
- `_UNVERIFIED_NUMERICAL` の `mesh_dependence` 行はそのまま維持する（未検証理由の記録用）。
- `application/_run_reconciliation.py:60-80` と `_preview.py:64-87` は `quality_status == "PASS"` を参照しているのみであるため、変更は不要と考えられる。念のため確認する。
- 試験：`tests/component/febio/test_required_quality_status.py` に「5項目PASS＋mesh_dependence UNVERIFIED → quality_status PASS」を1本追加する。`tests/e2e/test_installed_synthetic.py:2590` 付近の「fine 以外は mesh_dependence が UNVERIFIED であること」を検証する assert は維持し、`_ALLOWED_NUMERICAL`（94行目）の判定対象から `mesh_dependence` を除外する。

完了条件：1メッシュ・1実行で `quality_status=PASS`、`task_status=NEEDS_PREVIEW`（表示前）となること。

### T4：`preview` の `LAUNCHED`

現状：`cli/preview.py` および `application/_preview.py` は「外部観測者が既存の Studio セッションを確認して stdin で応答する」プロトコルのみであり、Studio を起動する処理は存在しない。

1. `application/_preview.py` に `launch_preview(service, case_id, *, manifest_id, studio_path) -> dict` を追加する。処理手順：manifest を解決して `output/results.xplt` の実パスとハッシュを検証 → `subprocess.Popen([studio_path, xplt_path])` を実行（シェル文字列は使用せず、`cwd` はケース領域外とする）→ `PreviewReceipt` を、`status=LAUNCHED`、`studio` のバージョン・パス・ハッシュ、PID、起動時刻とともに `storage/preview.py` の既存ストアへ保存 → 応答を返す。Studio の終了は待機しない。
2. `task_status` の導出：`_preview.py:64` の `receipt["status"] == "CONFIRMED"` を `in {"LAUNCHED", "CONFIRMED"}` に変更する。`_run_reconciliation.py:76-80` は `preview_summary` の `task_status` を参照しているのみのため、そのまま追従する。`domain/lifecycle.py:47` の `PreviewStatus` には `LAUNCHED` が既に定義されている。
3. CLI：`cli/main.py:113` の `preview` パーサーを、`--manifest-id`（必須）、`--studio`（必須）、`--window-id` / `--timeout`（任意。指定された場合は従来の観測プロトコルを使用）を受け付けるよう変更する。`cli/case.py` で `--window-id` の指定がない場合は `launch_preview` を呼び出す。
4. 試験：Studio 実行ファイルの代わりに引数を記録するのみのダミー `.exe`／`.bat` を `tmp_path` に配置し、`LAUNCHED` の記録と `task_status=COMPLETE` を確認する component 試験を1本追加する。実 Studio での起動確認は手動で記録する（T6）。

完了条件：`preview CASE --manifest-id M --studio FEBioStudio.exe --json` で Studio が起動し、`preview_status=LAUNCHED`、`task_status=COMPLETE` となること。

### T5：一貫試験の更新

`tests/e2e/test_installed_synthetic.py`（2,975行・1関数）を T1〜T4 の変更に合わせて更新する。

- 設定（`FEBIO_CAE_E2E_SETTINGS`）から `qualification_bundle` を除外し、`limits` を `preparation_calls: 1, solver_calls: 2` に変更した MVP 向けの設定を新たに用意する（3段階メッシュの設定は backlog 用に残して差し支えない）。
- `run-demo` → `run` とし、fine 以外の mesh_dependence 判定は T3 の方針に従う。
- 手順7として `preview`（ダミー Studio で可。実 Studio は手動で実施）を追加し、最終的な `task_status=COMPLETE` を確認する。
- 設定側で `installed_python` と `wheel` の SHA-256 を固定しているため、コードを変更するたびに `python -m build` → 新規 venv へのインストール → 設定内の SHA 更新を行う必要がある。この手順を `docs/cli-usage.md` に記載する。

完了条件：実FEBioにおいて `python -m pytest tests/e2e/test_installed_synthetic.py` が exit 0 で終了すること。

### T6：文書と記録

- `docs/cli-usage.md` に MVP の8手順に関する実行例（実際に使用したコマンドと JSON の要点）を記載する。
- `docs/reviews/2026-MM-DD-mvp-planar-e2e.md` に、対象コミット、wheel の SHA-256、実行コマンド、終了コード、`run_id` / `manifest_id`、FEBio のログ要約、Studio で開いたスクリーンショットの保存先（Git管理外）を記録する。
- 計画書 §3 の状態列を更新する。

## 4. やらないこと

- 球、円柱、摩擦、Neo-Hookean、ソースローカル細分化、LLM経路には手を加えない（backlog）。
- `adapters/geometry/_gmsh_runtime.py`（約4,200行の依存関係認証）には手を触れない。動作しているならそのまま利用する。
- 新たなハッシュ固定、自己ハッシュ、追加の権限レイヤーは導入しない。T2 はこれらを削減するための作業である。
- 試験は変更した契約の分のみ作成する。1機能につき数本で十分である。

## 5. 報告の形式

タスクごとに次の項目を報告すること：対象コミットSHA、変更ファイル、実行したコマンドと終了コード（`pytest` の件数）、実FEBio／Studio の起動回数、未検証事項、次回必要な判断事項。中断・省略した試験は合格数に含めない。
