# 製品版M1 readiness記録（範囲調査・文書化のみ／合意は未完了）

- 記録作成日（workstation local date）：2026-09-23
- 記録状態：`M1_PREPARATION_COMPLETE_AGREEMENT_PENDING`。M1の**準備**は完了したが、**製品範囲の合意は完了していない**。[製品版ロードマップ](../plans/2026-09-23-product-roadmap.md) §8.1 の3件は2026-09-23のPM回答で確定した一方、§8.3（Q3〜Q5・Q8＝実モデルの対象・使用許可・物理条件・精度基準・新規実行予算）は開発部長へ照会中で未回答であるため、合意完了を宣言しない
- 対象：`docs` のみ。`src`／`tests` の変更0、新機能0
- 実施者：単独worker 1名（Claude Opus 5／`claude-opus-5`）。サブエージェント0、他workerの起動0
- 実FEBio起動0、実FEBio Studio起動0、実Gmsh起動0、実LLM呼出0、実CAEモデル（`02_CAE`）への読み書き0

## 1. 対象SHA

| 対象 | 値 |
|---|---|
| ブランチ | `orca/acceptance-integration` |
| 開始時HEAD | `67cb7fe`（`docs: make the current-state lines consistent`） |
| 参照している製品source | `71c388f229dbfabc9fffb48c90c4ca9c1def5ee9` |
| 参照しているtest-only／format-only | `56e122b` ／ `0e35ba4d` |
| 参照しているnative final wheel | SHA-256 `f978de60803f70b9a5858a60bae185e0ed467949b0e062eafd2826a5e18b2cdd`／size `404193` |

本記録は既存の受入記録から上記の値を引用しているだけであり、これらに対する再検証（再build、再実行、再ハッシュ）は行っていない。

## 2. 変更ファイル

新規2件と索引の更新1件のみ。削除0、`src`／`tests` の変更0。

```text
docs/plans/2026-09-23-product-roadmap.md        （新規）
docs/reviews/2026-09-23-product-m1-readiness.md （新規・本書）
docs/reviews/README.md                          （現行記録の索引を3件へ更新）
```

`docs/reviews/README.md` は「2026-09-15時点では現行の記録はなし」という古い記述だけを、現行3件の索引へ置き換えた。他の記録内容は変更していない。

## 3. 実行したコマンドと終了コード

許容された確認のみを実行した。full `pytest`、`python -m build`、native実行、実 `02_CAE` の読み書き、Studio起動、既存caseの操作はいずれも0回である。

| # | 実コマンド | 終了コード | 件数・結果 |
|---:|---|---:|---|
| 1 | `git log --oneline -3` | 0 | HEAD=`67cb7fe` |
| 2 | `git status --porcelain` | 0 | 出力0行（調査開始時点で作業ツリーは clean） |
| 3 | `git diff --check` | 0 | 指摘0件 |
| 4 | `python scripts/scan_cae_data.py --root .`（新規docs追加前） | 0 | `status=PASS`、`checked_files=278`、`tracked_files=278`、`index_content_checked=278`、`diagnostics=0`、`issues=0` |
| 5 | `git add -A` → `git diff --check` | 0 | 指摘0件（CRLF変換の警告のみ） |
| 6 | `python scripts/scan_cae_data.py --root .`（新規docs追加後・commit直前） | 0 | `status=PASS`、`checked_files=280`、`tracked_files=280`、`index_content_checked=280`、`diagnostics=0`、`issues=0` |

上記のほかに、read-onlyの参照（`cat`／`sed -n`／`grep`／`find`／`wc` によるファイル読取と件数計数）を行った。これらは製品状態を変更せず、終了コードを受入証拠として扱わない。

### 3.1 read-onlyで得た静的計数（2026-09-23）

| 対象 | 値 |
|---|---|
| `src` の `.py` ファイル数 | 106 |
| `src` の総行数 | 40,975 |
| `tests` の総行数 | 40,041 |
| 試験関数の定義数 | unit 455／component 493／e2e 1／native 3 |
| `tests/native` のマーカー内訳 | `native` 2件、`llm` 1件、**`febio` 0件** |

関数定義数は、parametrize展開後の実行件数（最終full-suiteの `1798`）とは別物であり、合格件数として扱わない。本taskではpytestを一度も実行していない。

## 4. 実FEBio・Studio・その他ネイティブの起動回数

| 区分 | 本taskでの回数 |
|---|---:|
| 実FEBio 4.12.0 | 0 |
| 実FEBio Studio | 0 |
| 実Gmsh（`inspect --native`／`prepare-planar`） | 0 |
| 実LLM（OpenAI Responses） | 0 |
| 実CAEモデル（`02_CAE`）の読み書き | 0 |

以前のnative予算は消費済みであり、本taskでは流用も再利用もしていない。新規予算の**提案**は[ロードマップ §5](../plans/2026-09-23-product-roadmap.md)にあり、承認前の実行は行わない。

## 5. 調査で確定した不足（証拠：現行コードの読取）

[ロードマップ §2](../plans/2026-09-23-product-roadmap.md) の対応表に全件を記載した。実装上の具体点のみ再掲する。

| 項目 | 位置 | 観測した事実 |
|---|---|---|
| 物理適用性 | `src/febio_cae/application/_required_quality.py:327` | `physical_applicability_validation` は判定処理を持たず、理由付き `UNVERIFIED` を生成するだけである |
| surface approximation | `src/febio_cae/application/_preparation.py:453`、`src/febio_cae/adapters/geometry/adapter.py:1068` | `surface_approximation` は固定 `UNVERIFIED`。メッシュ品質レコード側も「双方向のCAD↔メッシュ境界は未確立」を理由に常に `UNVERIFIED`。片方向の `tool-boundary-deviation-upper-bound` だけがnative生成時に `PASS` で記録される |
| native qualification | `src/febio_cae/application/_inspection.py:84` | 調査応答の `native_qualification` は常に `UNVERIFIED`。FEBio側の `has_qualified_runtime`（`adapters/febio/_native_qualification.py:177`）に相当する束縛検査が調査経路には無い |
| メッシュ依存性 | `src/febio_cae/application/_mesh_refinement.py` | 3段階判定は実装済み。不足しているのは実行証拠（粗・中・細の実FEBio結果）だけである |
| Studio `CONFIRMED` | `src/febio_cae/cli/preview.py`、実装ノート §7 | `--window-id` ＋ stdin PIPEの観測経路は実装済みだが、観測応答を作る公開ヘルパーがCLIに無い |
| 実モデルE2E | `tests/e2e/` | 計画書 §7 が挙げる `test_authorized_real_case.py` と `test_bottomframe_final.py` が**存在しない** |
| E2E設定scope | `tests/e2e/test_installed_synthetic.py:1055` | 設定は `scope="synthetic_explicit"` のみ受理する。実モデル用のscopeが無い |
| 実ツール試験の入口 | `pyproject.toml`、`tests/native` | 計画書 §7 の `pytest tests/native -m "febio"` に該当する試験が0件である |
| 復旧 | `src/febio_cae/cli/main.py`（`resume` のヘルプ） | `resume` は「中断した同期公開を再開せずに診断する」であり、実行を再開する機能ではない |
| 結果の自然言語報告 | — | 実装も設計仕様書の契約行も存在しない |

## 6. 未検証事項（本taskで `UNVERIFIED` のまま残したもの）

- 本記録が引用する既存の受入値（full-suite `1798 passed`、22 stage全exit 0、wheel SHA-256、比較値など）は**再検証していない**。各canonical recordを正とする。
- `mesh_dependence`、`physical_applicability_validation`、`surface_approximation`、`native_qualification`、Studio表示内容は、MVP時点と同じく `UNVERIFIED` のままである。本taskはこれらを変更していない。
- 実モデルE2E（E2E-02）、最終BottomFrame E2E（E2E-03）、実LLM AI-02は未着手である。
- 静的計数（§3.1）はファイル読取に基づく数であり、試験の合格を意味しない。
- ロードマップ §3〜§6 の「提案」は、いずれもユーザー承認前の案であって製品契約ではない。

## 7. 質問への回答状況と、次に必要な判断

### 7.1 2026-09-23に回答済み（PM経由、確定）

| # | 内容 |
|---:|---|
| Q1 | 物理的な実物照合は既存仕様どおり数値品質とは別行に表示し、未検証なら `UNVERIFIED` のまま残す。`surface_approximation` は既存契約と数値証拠に従い、一律の必須化はしない |
| Q2 | Studio `CONFIRMED` の必須性は既存の製品契約で確定済みであり再承認不要。独立観測の要件は維持し、**観測主体はM2実行時に確定する** |
| Q6前半 | 「結果の簡潔な自然言語報告」は合意済みロードマップとユーザーの目的に含まれ、要否の新規承認は不要 |

### 7.2 未回答（依存作業は実行しない）

| # | 内容 | 状態 |
|---:|---|---|
| Q3 | E2E-02の対象実STEP、書込を許可する領域、部品と治具の役割 | PMから開発部長へ照会中 |
| Q4 | E2E-02／03の物理条件（材料・支持・押し込み量・接触対・摩擦・評価領域・許容差） | 同上。**推測しない。**一括承認では未指定を解消できないため、一括承認の選択肢は撤去した |
| Q5 | 実モデルでの精度の合格基準 | 同上 |
| Q8 | 新規の実ツール実行予算 | 同上。**以前のnative予算は消費済みであり流用しない** |

M2着手時に確定する観測主体（Q2b）、M4着手前に確定するLLM設定（Q6後半）、M5着手前に合意する限定利用の定義（Q7）は、該当milestoneのゲートとして[ロードマップ §8.2](../plans/2026-09-23-product-roadmap.md) に提案のまま保持している。

### 7.3 次に必要な判断

1. **Q3〜Q5・Q8の回答**。これが無い限りM3（実モデルE2E）は着手できず、M1の合意完了も宣言できない。
2. **M2の着手対象**：(a) Studio `CONFIRMED`、(b) 3段階mesh依存、(c) 適用性・近似・認定のどれを、どの順で行うか。**おすすめは (b) から**（コード変更0、実モデルの使用許可が不要、予算はメッシュ3／実FEBio 4）。
3. **統合**：`git push` とV2への統合はPMが担当する。本taskでは実施しない。

## 8. 境界の保持

- 旧manual `case-6294a0a9e02c`、受理済みautomated `case-80e5f42a5043`、manual final `case-bb9e975f3fb3` はいずれも参照のみで、retry／reset／状態変更／証拠の付け替えを行っていない。
- 保護ブランチ `native-curved-primitives`／`acceptance-gate-repairs` およびsoft-holdの作業ツリーは削除・変更していない。
- 公開文書にローカルパス・資格情報・実CAEデータを記載していない（§3 の `scan_cae_data.py` はPASS）。
- 合成データの結果と実モデルの証拠を混同していない。未取得の項目は `UNVERIFIED` のまま保持している。
