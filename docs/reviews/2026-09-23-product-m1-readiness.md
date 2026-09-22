# 製品版M1 readiness記録（M2予算合意済み・M3条件待ち）

- 記録作成日（workstation local date）：2026-09-23
- 現在地：**M1-B＝M2予算合意済み・M3条件待ち**。承認 `msg_757df3e48e7a` によりM2 inspection 1／preparation 3／実FEBio 4＋別枠Studio起動1は実行可能（旧case保持、失敗停止、再試行0）。同一予算の再承認は不要。M3は対象・使用許可・物理条件・評価基準が未提供で `ASK_AND_BLOCK`、実モデル操作は行わない。製品全体またはM1全体の合意完了ではない
- 履歴：以下§1〜§6・§8〜§9のM1調査／仕上げ実績はdocsのみ（`src`／`tests` 変更0、新機能0）。当時は `M1_PREPARATION_COMPLETE_AGREEMENT_PENDING` だった。今回のブリッジ準備と検証は[M2 preparation記録](2026-09-23-product-m2-preview-preparation.md)へ分離し、過去実績に付け替えない
- 実施者：初稿調査はClaude Opus 5、前任者の終了後にPMが編集所有権を移管し、本改訂はCodex workerが仕上げた。同時docs編集者1名、本workerによる他worker起動0。運用監視機構は別担当
- 実FEBio起動0、実FEBio Studio起動0、実Gmsh起動0、実LLM呼出0、実CAEモデル（`02_CAE`）への読み書き0

## 1. 対象SHA

| 対象 | 値 |
|---|---|
| ブランチ | `orca/acceptance-integration` |
| 初稿調査開始時HEAD | `67cb7fe`（`docs: make the current-state lines consistent`） |
| 本改訂開始時HEAD | `a31a87b83a4dec7c4e20893d11782e9fb1304e6d`（M1初稿）。前任者のroadmap未コミット改訂を保持して引継ぎ |
| 参照している製品source | `71c388f229dbfabc9fffb48c90c4ca9c1def5ee9` |
| 参照しているtest-only／format-only | `56e122b` ／ `0e35ba4d` |
| 参照しているnative final wheel | SHA-256 `f978de60803f70b9a5858a60bae185e0ed467949b0e062eafd2826a5e18b2cdd`／size `404193` |

本記録は既存の受入記録から上記の値を引用しているだけであり、これらに対する再検証（再build、再実行、再ハッシュ）は行っていない。

## 2. 変更ファイル

初稿 `a31a87b` は成果物2件の新規作成と `docs/reviews/README.md` の索引更新を含む。本仕上げは次の既存2件のみを更新し、索引と過去の受入記録は変更しない。削除0、`src`／`tests` の変更0。

```text
docs/plans/2026-09-23-product-roadmap.md
docs/reviews/2026-09-23-product-m1-readiness.md
```

## 3. 実行したコマンドと終了コード

許容された確認のみを実行した。full `pytest`、`python -m build`、native実行、実 `02_CAE` の読み書き、Studio起動、既存caseの操作はいずれも0回である。以下の表は**初稿から引き継いだ実績**であり、仕上げworkerの再実行結果ではない。

| # | 実コマンド | 終了コード | 件数・結果 |
|---:|---|---:|---|
| 1 | `git log --oneline -3` | 0 | HEAD=`67cb7fe` |
| 2 | `git status --porcelain` | 0 | 出力0行（調査開始時点で作業ツリーは clean） |
| 3 | `git diff --check` | 0 | 指摘0件 |
| 4 | `python scripts/scan_cae_data.py --root .`（新規docs追加前） | 0 | `status=PASS`、`checked_files=278`、`tracked_files=278`、`index_content_checked=278`、`diagnostics=0`、`issues=0` |
| 5 | `git add -A` → `git diff --check` | 0 | 指摘0件（CRLF変換の警告のみ） |
| 6 | `python scripts/scan_cae_data.py --root .`（新規docs追加後・commit直前） | 0 | `status=PASS`、`checked_files=280`、`tracked_files=280`、`index_content_checked=280`、`diagnostics=0`、`issues=0` |

初稿は上記以外にread-only参照と件数計数を記録していた。その実行ログを本仕上げで再検証しておらず、初稿の記録として保持する。

### 3.1 read-onlyで得た静的計数（2026-09-23）

| 対象 | 値 |
|---|---|
| `src` の `.py` ファイル数 | 106 |
| `src` の総行数 | 40,975 |
| `tests` の総行数 | 40,041 |
| 試験関数の定義数 | unit 455／component 493／e2e 1／native 3 |
| `tests/native` のマーカー内訳 | `native` 2件、`llm` 1件、**`febio` 0件** |

上表は初稿の静的計数の引用であり、本仕上げで再計数していない。関数定義数はparametrize展開後の実行件数（最終full-suiteの `1798`）とは別物であり、合格件数として扱わない。本M1でpytestは実行していない。

### 3.2 本仕上げで実行した確認

| 実コマンド | 終了コード | 件数・結果 |
|---|---:|---|
| `git status --short --branch`、`git rev-parse HEAD` | 0 | `a31a87b`。roadmap既存改訂1件を確認し、PMから所有移管を受領 |
| `git diff -- docs/plans/2026-09-23-product-roadmap.md`、`git log -4 --oneline` | 0 | 既存改訂148追加／120削除。初稿commitと未コミット成果を保存して仕上げ |
| `git diff --check` | 0 | 指摘0件。LF→CRLFの警告のみ |
| `python scripts/scan_cae_data.py --root .` | 0 | `PASS`、checked 280／tracked 280／index 280、diagnostics 0／issues 0 |

上記scannerはroadmap改訂後の実行。本記録の更新後にも同じ2検査を最終候補に実行し、最終結果は§9に記録する。ソース・試験のread／glob／grepは静的参照のみで、pytest収集・実行や製品の起動はしていない。Orcaのguide／check／ask／heartbeat等は作業連絡であって製品受入コマンドではない。

## 4. 実FEBio・Studio・その他ネイティブの起動回数

| 区分 | 本taskでの回数 |
|---|---:|
| 実FEBio 4.12.0 | 0 |
| 実FEBio Studio | 0 |
| 実Gmsh（`inspect --native`／`prepare-planar`） | 0 |
| 実LLM（OpenAI Responses） | 0 |
| 実CAEモデル（`02_CAE`）の読み書き | 0 |

以前のnative予算は消費済みであり、M1調査では流用も再利用もしていない。現在のM2予算は別途承認済み（冒頭参照）。後続milestoneの予算は[ロードマップ §5](../plans/2026-09-23-product-roadmap.md)の提案のままである。

## 5. 調査で確定した不足（証拠：現行コードの読取）

[ロードマップ §2](../plans/2026-09-23-product-roadmap.md) の対応表に全件を記載した。実装上の具体点のみ再掲する。

| 項目 | 位置 | 観測した事実 |
|---|---|---|
| 物理適用性 | `src/febio_cae/application/_required_quality.py:326-332` | 理由付き `UNVERIFIED` を数値品質とは別行に公開する。既存契約に沿った状態表示であり、実物照合未実施を新たな必須品質失敗にしない |
| surface approximation | `src/febio_cae/application/_preparation.py:445-456`、`src/febio_cae/adapters/geometry/adapter.py:1067-1078` | `UNVERIFIED`。片方向のprimitive境界証拠は双方向CAD↔mesh近似証拠ではない。適用範囲を既存契約と数値証拠から整理し、一律必須化しない |
| native qualification | `src/febio_cae/application/_inspection.py:84`、`_preparation.py:455` | 応答は `UNVERIFIED`。Gmshの実行時identity認証と幾何能力のqualificationを区別する。`adapters/febio/_native_qualification.py` のFEBio runtime束縛をGmsh能力証拠へ流用しない。新たなハッシュ固定・権限層は提案しない |
| メッシュ依存性と製品判定 | `src/febio_cae/application/_mesh_refinement.py`、`_required_quality.py:75-124`、`_preview.py:73-103` | 3段階算定は実装済みだが、MVP判定はglobal mesh `UNVERIFIED` とStudio `LAUNCHED` を許容する。実証取得と製品判定への切替は別作業であり、「不足は証拠だけ」「無変更で合格」とは保証しない |
| Studio `CONFIRMED` | `src/febio_cae/cli/preview.py`、実装ノート §7 | M1調査時点では公開ヘルパー無し。今回 `scripts/observe_preview.py` でPIPEへの公開ブリッジを準備したが、実UI証拠は未取得 |
| 実モデルE2E | `tests/e2e/` | 計画書 §7 が挙げる `test_authorized_real_case.py` と `test_bottomframe_final.py` が**存在しない** |
| E2E設定scope | `tests/e2e/test_installed_synthetic.py:1055` | 設定は `scope="synthetic_explicit"` のみ受理する。実モデル用のscopeが無い |
| 実ツール試験の入口 | `pyproject.toml`、`tests/native` | 計画書 §7 の `pytest tests/native -m "febio"` に該当する試験が0件である |
| 復旧 | `src/febio_cae/cli/run.py`、`application/service.py:1128-1131`、`application/_run_reconciliation.py` | 公開経路は限定的な中断照合を行う。ソルバーの途中計算再開の実証ではない。実ツールでの復旧受入証拠は現行2記録に無い |
| 部分変更 | `src/febio_cae/domain/case_patch.py:27-39`、`application/service.py:478-526` | 材料以外の型付きトップレベル差分も受理する。現行native受入証拠がE-onlyに限られることと、型の実装範囲を混同しない |
| AI-02 | `tests/native/test_llm.py:273-518` | 合成fixtureに対する材料3操作と明示freezeの試験。実LLM6呼出を期待するが、CAD新規入力・native解析・結果報告は含まない。本M1では未実行 |
| 結果の自然言語報告 | ロードマップ §3 M4 | 現行の該当実装を確認できていないが、ユーザー目的と合意済み順序に含まれ、要否の再承認は不要。M4で出力契約を具体化する |

## 6. 未検証事項（本taskで `UNVERIFIED` のまま残したもの）

- 本記録が引用する既存の受入値（full-suite `1798 passed`、22 stage全exit 0、wheel SHA-256、比較値など）は**再検証していない**。各canonical recordを正とする。
- `mesh_dependence`、`physical_applicability_validation`、`surface_approximation`、`native_qualification`、Studio表示内容は、MVP時点と同じく `UNVERIFIED` のままである。本taskはこれらを変更していない。
- 実モデルE2E（E2E-02）、最終BottomFrame E2E（E2E-03）、実LLM AI-02の合格証拠は、参照した現行受入記録に無い。全過去実行の不存在を主張しない。
- 静的計数（§3.1）は初稿の引用であり、試験の合格を意味しない。
- ロードマップの「提案」は承認前の順序・実施方法・予備見積である。既存仕様の製品範囲、Studio `CONFIRMED`、自然言語報告の要否まで未確定扱いに戻さない。M3の収束・子版表示、M4の解析接続等の予算は別途精査が必要で、予備合計を全体の確定総額としない。

## 7. 質問への回答状況と、次に必要な判断

### 7.1 2026-09-23に回答済み（PM経由、確定）

| # | 内容 |
|---:|---|
| Q1 | 物理的な実物照合は既存仕様どおり数値品質とは別行に表示し、未検証なら `UNVERIFIED` のまま残す。`surface_approximation` は既存契約と数値証拠に従い、一律の必須化はしない |
| Q2 | Studio `CONFIRMED` の必須性は既存の製品契約で確定済みであり再承認不要。独立観測の要件は維持し、観測主体はPMに確定した（手順は実装ノート §7） |
| Q6前半 | 「結果の簡潔な自然言語報告」は合意済みロードマップとユーザーの目的に含まれ、要否の新規承認は不要 |
| Q8（M2分） | `msg_757df3e48e7a` によりinspection 1／preparation 3／実FEBio 4＋別枠Studio起動1を承認。旧case保持、失敗停止、再試行0。同一予算の再承認は求めない |

### 7.2 未回答（依存作業は実行しない）

| # | 内容 | 状態 |
|---:|---|---|
| Q3 | E2E-02の対象実STEP、書込を許可する領域、部品と治具の役割 | PMから開発部長へ照会中 |
| Q4 | E2E-02／03の物理条件（材料・支持・押し込み量・接触対・摩擦・評価領域・許容差） | 同上。**推測しない。**一括承認では未指定を解消できないため、一括承認の選択肢は撤去した |
| Q5 | 実モデルでの精度の合格基準 | 同上 |
| Q8（後続分） | M3〜M5の新規実ツール予算 | 将来の提案。M2分は回答済みでありM2を止めない。以前のnative予算は流用しない |

M2の観測主体（Q2b）はPMに確定し、実観測は未実施。M4着手前のLLM設定（Q6後半）、M5着手前の限定利用の定義（Q7）は、[ロードマップ §8.2](../plans/2026-09-23-product-roadmap.md) に提案のまま保持する。

### 7.3 次に必要な判断

1. **Q3〜Q5の具体的回答**。これが無い限りM3（実モデルE2E）は `ASK_AND_BLOCK`。判断委任は未指定の物理条件を推測する許可ではない。
2. **M2は着手可能**：(b) 3段階meshのnative証拠取得と(a)公開ブリッジ準備を進め、PMが実desktop UIの独立観測を別枠Studio 1回で行う。(c)は解析的調査のみ。準備試験の合格を実Studio `CONFIRMED` やM2完了へ読み替えない。
3. **統合**：`git push` とV2への統合はPMが担当する。本taskでは実施しない。

## 8. 境界の保持

- 旧manual `case-6294a0a9e02c`、受理済みautomated `case-80e5f42a5043`、manual final `case-bb9e975f3fb3` はいずれも参照のみで、retry／reset／状態変更／証拠の付け替えを行っていない。
- 保護ブランチ `native-curved-primitives`／`acceptance-gate-repairs` およびsoft-holdの作業ツリーは削除・変更していない。
- 公開文書にローカルパス・資格情報・実CAEデータを記載していない（§3 の `scan_cae_data.py` はPASS）。
- 合成データの結果と実モデルの証拠を混同していない。未取得の項目は `UNVERIFIED` のまま保持している。
- Q3〜Q5は依然未回答。M2分Q8は回答済みで重複承認を求めない。Q6後半・Q7を理由にM1文書準備や承認済みM2作業を止めない。
- Codex週間usedが60%以上なら新規継続停止、Opus／Geminiは継続可、reset後の重複なし再開とstop/resume inbox報告はPM担当。本workerはその運用機構を変更していない。仕上げ中の承認済みquota参照では `2026-09-22T16:14:27.483Z`、10080分window、used 44%を確認した（取得元のローカルパスは公開しない）。

## 9. 仕上げの検証結果と候補の識別

本文整合後に `git diff --check && python scripts/scan_cae_data.py --root .` を実行し、終了コード0。diff指摘0、scanner `PASS`、checked／tracked／indexは各280、diagnostics／issuesは各0。改行のLF→CRLF警告のみで、製品コード・試験・実データへの変更は無い。

本節はM1-A仕上げ時点の履歴である。対象sourceと初稿SHAは§1に固定し、当時の仕上げcommitの実SHA・最終index検査結果・commit後の差分有無はそのRun完了通知を正とする。当時の到達点はM1-AでQ3〜Q5・Q8が未回答だった。現在は冒頭・§7のとおりM2予算合意済み・M3条件待ちであり、今回の検証・commitは[M2 preparation記録](2026-09-23-product-m2-preview-preparation.md)と今回Run完了通知に分離する。V2統合・pushはPM担当であり、本workerは実施しない。
