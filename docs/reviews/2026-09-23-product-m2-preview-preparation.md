# M2(a) 公開観測ブリッジ準備／M1-B更新

## 対象と境界

- 基準source：`994ef9356e545fe5ea280e5a04386af36d698fe0`、作業ブランチ `orca/acceptance-integration`。候補commitの実SHAはこの記録を含むRun完了通知に記載する（自己参照値を捏造しない）。
- M1-B＝**M2予算合意済み・M3条件待ち**。承認 `msg_757df3e48e7a` はM2 inspection 1／preparation 3／実FEBio 4＋別枠Studio 1、旧case保持、失敗停止・再試行0。同一予算の再承認は求めない。全体合意またはM2完了の宣言ではない。
- 本workerはbridge/docsのみ。別native workerのcase・実行領域・wheelには触れず、追加worker 0、実Gmsh 0、実FEBio 0、実Studio 0、実LLM 0、実モデル操作0。full pytest／build／push／V2統合も0。
- M3の物理条件等は未提供で `ASK_AND_BLOCK`。後続milestone予算は提案のまま、判断委任から条件を推測しない。

## 変更ファイル（6件）

- `scripts/observe_preview.py`：installed public previewをPIPEで起動する小さな標準ライブラリhelper。実要求とstdoutを公開し、独立操作者の応答1件をそのまま中継。GUI操作、PNG生成、観測値補完、receipt上書きはしない。
- `tests/component/cli/test_preview_bridge.py`：3 fixture試験。実PIPE要求の透過公開＋Unicode応答、再使用ディレクトリ拒否（子を起動しない）、無応答での既存期限と終了コード伝播。
- `docs/specs/implementation-notes.md`：§7に実argv、応答全フィールド、120秒以内の期限、外部ファイル交換手順と限界。
- `docs/plans/2026-09-23-product-roadmap.md`：M2承認済み／M3条件待ち、PM独立観測、ブリッジ準備と実UI未検証を分離。
- `docs/reviews/2026-09-23-product-m1-readiness.md`：同じ現在地へ整合、以前の合意待ちを履歴として保持。
- 本記録。

製品 `src`、ストア、`LAUNCHED`／MVP判定、既存観測バリデーションは変更0。相関・対象・表示値・PNG鮮度の検証をhelperへ複製しない。新規exchange directoryとrequest前応答の拒否だけをhelper側で行う。データパス・request／response／PNGはGit外。

## 実行証拠

以下は本workerの実行。fixture／smokeは実Studioや実モデルの成功証拠ではない。

| コマンド | exit | 結果 |
|---|---:|---|
| `git status --short --branch`、`git rev-parse HEAD` | 0 | 開始時clean、基準SHAは上記 |
| `.venv/Scripts/python.exe -m pytest -q tests/component/cli/test_preview_bridge.py tests/component/cli/test_preview_cli.py tests/component/febio/test_preview_existing_session.py` | 0 | 21 passed、1.82 s |
| `.venv/Scripts/python.exe -m ruff format scripts/observe_preview.py tests/component/cli/test_preview_bridge.py` | 0 | 1 file reformatted、1 unchanged |
| `.venv/Scripts/python.exe -m ruff check scripts/observe_preview.py tests/component/cli/test_preview_bridge.py` | 0 | All checks passed |
| `.venv/Scripts/python.exe -m pytest -q tests/component/application/test_preview_flow.py`（初回、実行ラッパー上限120秒） | 終了コード未取得 | 120.12秒でラッパーtimeout。5 progress dotsは合格件数に計上しない。再実行結果は下記に分離 |
| `.venv/Scripts/python.exe -m pytest -q tests/component/application/test_preview_flow.py`（再実行、ラッパー上限360秒） | 0 | 8 passed、197.88 s。既存の古いPNG／異なるnonce拒否とLAUNCHED経路を検証。製品timeout・native予算は変更0 |
| `.venv/Scripts/python.exe -m ruff format --check scripts/observe_preview.py tests/component/cli/test_preview_bridge.py` | 0 | 2 files already formatted |
| `.venv/Scripts/python.exe -m mypy scripts/observe_preview.py tests/component/cli/test_preview_bridge.py` | 0 | no issues、2 source files |
| `.venv/Scripts/python.exe scripts/observe_preview.py --help` | 0 | 実CLIヘルプ、既存HWND必須と120秒上限を確認 |
| `.venv/Scripts/python.exe -c <下記smoke program>`（Evalからargv配列で実行） | 0 | 実 `capture_observation` のPREVIEW_REQUESTED→NEEDS_PREVIEW、子exit 7を保存 |
| `git diff --check && git diff --cached --check` | 0 | 指摘0 |
| `python scripts/scan_cae_data.py --root .`（6ファイルstage後） | 0 | PASS、checked／tracked／index各283、diagnostics／issues各0 |

合格を確認した試験は21＋8＝29件（新規3、既存26）。初回timeoutは非PASSとして残す。記録追記後もstageした同じ6ファイルにdiff check／scannerを適用し、最終index結果とclean commitはRun完了通知を正とする。

smokeの `-c` に渡したprogram（仮の確認receiptや画像は作らず、OS一時領域を終了時に自動削除）：

```python
import os, runpy, sys, tempfile
from pathlib import Path
os.environ['PYTHONPATH'] = str(Path('src').resolve())
bridge = runpy.run_path('scripts/observe_preview.py')['bridge']
child = "import json,sys; from febio_cae.cli.preview import capture_observation\ntry: capture_observation({'smoke':'no-native'},0.1)\nexcept TimeoutError: print(json.dumps({'status':'NEEDS_PREVIEW'}));sys.exit(7)"
with tempfile.TemporaryDirectory() as root:
    code = bridge([sys.executable, '-c', child], Path(root) / 'exchange', 0.1)
    assert code == 7, code
    print('SMOKE PASS: real finite PIPE deadline, child exit 7 preserved; native 0')
```

継続admissionは修正済み `python <QUOTA_GUARD> --admission`（余分な引数無し）でALLOW／exit 0を取得した。実ローカルパスと運用監視の記録はGit外のRunに保持し、監視機構は変更しない。

## 次のPM操作・未検証

実argvと全応答フィールドは[実装ノート §7](../specs/implementation-notes.md#7-studio起動と観測case-preview)を正とする。PMは承認済みStudio 1回を起動し、対象ファイル・表示設定・版・HWNDを実UIで先に確認してから次を起動する。

```text
python scripts/observe_preview.py --cli "<INSTALLED_FEBIO_CAE_EXE>" --exchange-dir "<NEW_EXTERNAL_EXCHANGE>" --state-dir "<STATE_DIR>" --case-id "<CASE_ID>" --manifest-id "<MANIFEST_ID>" --studio "<STUDIO_EXE>" --window-id <HWND> --timeout 120
```

`request.json` は子CLIの実一回限り要求。PMはその後に新PNGを撮影し、独立観測値と相関IDを持つUTF-8・BOM無しのJSON object 1行を `response.tmp` に作り、同じディレクトリ内で `response.json` へrenameする。残り時間は実要求の `remaining_seconds`（観測開始から最大120秒、要求出現後120秒ではない）。PNG／交換領域はcase・Git外に保管し、要求値の自動転記で観測したことにしない。

実UI、実PNG、installed wheelとの実Studio連携、`CONFIRMED` は **UNVERIFIED／未実施**。終了コード0だけで製品完了にせず、最終JSONの `preview_status` と品質を確認する。失敗・タイムアウトは停止、再試行0。M2 native実績は別担当の記録を正とし、本記録から推測しない。PMがmilestone単位で統合・pushを判断する。
