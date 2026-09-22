# FEBio CAEハーネス — 製品版ロードマップ M1〜M5（合意用草案）

作成日：2026-09-23／現在地：**M1-B＝M2予算合意済み・M3条件待ち**。承認 `msg_757df3e48e7a` によりM2は着手可能（inspection 1／preparation 3／実FEBio 4、別枠Studio起動1、失敗停止・再試行0）。§8.3 の実モデル対象・使用許可・物理条件・評価基準は未回答のためM3は `ASK_AND_BLOCK`。製品全体またはM1全体の合意完了は宣言しない。
実測更新：**M2(b)合格・M2(c)調査済み、M2(a)は起動前競合拒否後の続行判断待ち**。実FEBio4回成功、fine／E-only子の数値6項目PASS。Studio実起動0・予約1保持。[M2実測記録](../reviews/2026-09-23-product-m2-evidence.md)を正とし、M2全体の完了は宣言しない。
ファイル名の日付は識別子である。参照順序は[開発契約](../../AGENTS.md) → [計画書](2026-09-14-febio-cae-harness-greenfield-plan.md) → [設計仕様書](../specs/2026-09-14-febio-llm-cae-harness-design-v2.md) → [実装ノート](../specs/implementation-notes.md) → [canonical review](../reviews/2026-09-20-mvp-planar-e2e.md)・[manual 8実行記録](../reviews/2026-09-22-mvp-manual-final.md)。本書とこれらが食い違う場合は既存文書を正とする。

初回調査の対象コミットは `67cb7fe`（ブランチ `orca/acceptance-integration`）。初稿commit `a31a87b83a4dec7c4e20893d11782e9fb1304e6d` と前任者の未コミット改訂を保持してM1文書を整合した。M1調査・仕上げでは `src`／`tests` の変更と実ツール・実モデル操作は0だった（[M1 readiness記録](../reviews/2026-09-23-product-m1-readiness.md)）。今回のM2ブリッジ準備・試験は[別記録](../reviews/2026-09-23-product-m2-preview-preparation.md)に分離し、過去の実績を変更しない。

## 0. 表記と読み方

| 表記 | 意味 |
|---|---|
| **既存契約** | 設計仕様書・計画書・開発契約に既に書かれている製品の約束。本書は再承認を求めず、勝手に狭めもしない |
| **必須証拠** | 既存契約を満たすために取得が必要な、実ツール・実モデルの証拠 |
| **ケース固有条件** | 対象モデルごとに利用者が与える値（材料・支持・荷重・評価領域・許容差など）。推測しない |
| **任意検証** | 既存契約が要求していない追加確認。実施はユーザー判断 |
| **提案** | 本書で新たに提案する内容。ユーザーが承認するまで製品契約ではない |
| **ASK_AND_BLOCK** | 記録から確定できず推測もしない項目。回答がないと当該作業を開始できない |

状態語は開発契約の規則に従う。`INSPECTED`＝観測完了、`PROVISIONED`＝登録完了、`PREPARED`＝準備記録の公開、`SUCCEEDED`＝終端到達と出力完全性、`LAUNCHED`＝表示ソフトの起動、`CONFIRMED`＝表示内容の独立観測、`COMPLETE`＝品質・表示を含む完了。合成データ・模擬ログの合格を、実FEBio・実Studio・実モデルの成功証拠に読み替えない。証拠不足は `UNVERIFIED` のまま残す。

本書が「現行受入証拠 未取得」と書く箇所は、[canonical review](../reviews/2026-09-20-mvp-planar-e2e.md) と[manual 8実行記録](../reviews/2026-09-22-mvp-manual-final.md)という現行の受入記録に該当証拠が無いという意味である。過去ログを全数調査した結果ではないため、「一度も行われていない」とは主張しない。

Git外の証拠rootは記号で参照する（`<COORDINATION>`＝元のチェックアウト/.local/coordination、`<ROOT_PYTEST>`＝元のチェックアウト/.local/pytest-basetemp）。実パス・資格情報・実CAEデータは本書に書かない。

## 1. 現在地と、既に確定している製品範囲

### 1.1 現在地（確定）

- MVP（計画書 §2 の8手順）は完了している。自動flowは[canonical review](../reviews/2026-09-20-mvp-planar-e2e.md)（`case-80e5f42a5043`、22 stage全exit 0）、手動flowは[manual 8実行記録](../reviews/2026-09-22-mvp-manual-final.md)（`case-bb9e975f3fb3`、公開CLI 22コマンド全exit 0）を正とする。
- 現行の版は source=`71c388f`、test-only=`56e122b`、format-only=`0e35ba4d`、docs head=`67cb7fe`。native final wheel SHA-256=`f978de60803f70b9a5858a60bae185e0ed467949b0e062eafd2826a5e18b2cdd`／size=`404193`。
- 最終標準full-suiteは `1798 passed / 0 failed / 3493.86 s`（`56e122b`、exit 0）。
- **製品版は未完了である。** MVPの達成は最終project完成を意味しない（計画書 §2、manual 8記録 §6）。

### 1.2 既に製品契約になっている範囲（既存契約・再承認不要）

設計仕様書 §1「目標範囲（MVP後）」および §6・§7 は、次を**既定の製品契約**として定めている。本書はこれらを未確定扱いにせず、全面的な再承認も求めず、**今回の作業で範囲から外しもしない**。

| 区分 | 既存契約の内容 |
|---|---|
| 環境 | Windows、Python 3.12、GUIなしのCLI。FEBio・FEBio Studioは外部導入 |
| 治具 | 球・円柱・直方体の剛体1つ。初期接触配置の自動計算 |
| 材料 | 等方線形弾性、圧縮性Neo-Hookean |
| 接触 | 摩擦なし、または明示された一定Coulomb摩擦 |
| 編集 | 材料、支持・接触領域、摩擦、治具、運動、メッシュ、評価条件の変更 |
| 入力 | 日本語による条件入力・質問・回答（型付き提案） |
| 品質 | メッシュ依存性を含む6項目の判定 |
| 表示 | Studio表示内容を独立確認した `CONFIRMED` |
| 完了の定義 | 物理的な実物照合の状態は別行に表示する（設計仕様書 §1「完了の定義」・§6） |

したがって本書は次の2点を守る。

1. **初回リリースへ何をどの順で含めるかの提案は、契約そのものの変更ではなく「契約との差分（順序と時期）」として扱う。** 範囲の削除は提案しない。
2. **物理的な実物照合を含む全 `UNVERIFIED` の一律解消を、製品完了の新しいゲートにしない。** 実物照合は既存どおり別行で表示し、未検証なら `UNVERIFIED` のまま残す。

## 2. 現状の対応表（製品能力 ↔ コード ↔ 試験 ↔ 現行受入証拠）

静的な規模（2026-09-23、read-only計数）：`src` 106ファイル／約40,975行、`tests` 約40,041行。試験関数の定義数は unit 455、component 493、e2e 1、native 3。実行時の試験件数（parametrize展開後）は最終full-suiteの `1798` であり、関数定義数と混同しない。

| # | 製品能力 | コード（正） | 試験 | 現行受入証拠 | 状態 | 具体的な不足 |
|---:|---|---|---|---|---|---|
| 1 | ケース登録・草案世代・不変版・原子的保存 | `domain/*`、`application/service.py`、`storage/*` | `tests/unit/contracts`、`tests/component/storage` | 合成ケースのみ | 実装済 | 実モデルでの適用は現行受入証拠 未取得 |
| 2 | STEP調査（`inspect --native`） | `adapters/geometry/inspection.py`、`_gmsh_runtime.py` | `tests/component/geometry` | 実Gmsh 4.15.2で `INSPECTED`（合成STEP、自動1・手動1） | 実装済 | `native_qualification` は `application/_inspection.py:84` で `UNVERIFIED` 固定。対応資格を満たす証拠と、その解消手順が未整理 |
| 3 | 平面準備（Tet10部品＋直方体剛体） | `adapters/geometry/preparation.py`、`adapters/geometry/adapter.py` | `tests/component/geometry`、`tests/component/application/test_planar_preparation.py` | 実Gmshで `PREPARED`（合成、自動1・手動1） | 実装済 | `surface_approximation` は `application/_preparation.py:453` で `UNVERIFIED` 固定。`adapters/geometry/adapter.py:1068` のレコードも常に `UNVERIFIED`（理由＝双方向のCAD↔メッシュ境界が未確立）。片方向の `tool-boundary-deviation-upper-bound` のみnative生成時に記録される |
| 4 | 対応表（組み込み既定バンドル） | `resources/planar_default_bundle.json`、`application/_profile_provisioning.py` | `tests/component/febio/test_profile_*.py` | `PROVISIONED`（合成、自動1・手動1） | 実装済 | 現行の能力範囲は `febio.scope.planar_linear_frictionless_fixed_xyz` のみ。既存契約にある球・円柱・摩擦・Neo-Hookeanの対応表は未整備 |
| 5 | 入力生成・所有プロセス実行・出力固定 | `adapters/febio/compiler.py`、`runner.py`、`_windows_job.py` | `tests/component/febio` | 実FEBio 4.12.0で `SUCCEEDED`（合成、自動2・手動2） | 実装済 | 実モデルでの実行は現行受入証拠 未取得 |
| 6 | 必須数値品質5項目 | `adapters/febio/quality.py`、`planar_quality.py`、`reported_norms.py`、`application/_required_quality.py` | `tests/component/febio/test_required_quality_status.py` ほか | 必須5項目 `PASS`（合成、自動1・手動1） | 実装済 | 実モデルでの判定は現行受入証拠 未取得。参照解は計画書 §5 の弾性パッチのみ |
| 7 | メッシュ依存性 `mesh_dependence`（既存契約の6項目目） | `application/_mesh_refinement.py`（3段階判定・局所球版を実装） | `tests/component/febio/test_mesh_refinement.py`、`tests/component/geometry/test_native_local_refinement.py` | 新M2合成flowで3段階実FEBio＋E-only継承、fine／child PASS（[M2実測記録](../reviews/2026-09-23-product-m2-evidence.md)） | 実装済・平面合成で実証 | 実モデル・他形状への証拠は未取得 |
| 8 | 物理適用性 `physical_applicability_validation` | `application/_required_quality.py:327` が理由付き `UNVERIFIED` を生成し、数値集計とは**別行**で公開する（既存契約どおり） | `tests/component/application/test_required_numerical_quality.py:151` | なし（別行表示のため数値品質の合否には影響しない） | 既存契約どおり | 実物照合の証拠が無い限り `UNVERIFIED` のまま。解消するには実測との照合が要る |
| 9 | Studio表示 `LAUNCHED` | `adapters/preview/studio.py`、`application/_preview.py` | `tests/component/febio/test_preview_binding.py` ほか | 実Studioで `LAUNCHED`／`task COMPLETE`（合成、自動1・手動1） | 実装済 | 表示内容は `UNVERIFIED`。receiptの `version` も `UNVERIFIED` |
| 10 | Studio表示 `CONFIRMED`（既存契約） | `cli/preview.py`（`--window-id` ＋ stdin PIPE）、`scripts/observe_preview.py`、`domain/preview.py:166`、`application/_preview.py` | `tests/component/febio/test_preview_existing_session.py`、`tests/component/cli/test_preview_bridge.py` | 実UI証拠は未取得 | 経路・公開ブリッジ実装済 | PMが独立観測者として実UIを確認し、要求後のPNGと応答を期限内に渡す（実装ノート §7）。準備検証は実Studioの成功証拠ではない |
| 11 | 部分変更と比較 | `domain/case_patch.py`、`application/service.py:478-526`、`application/_comparison.py`、`cli/compare.py` | `tests/component/cli/test_case_patch_cli.py`、`tests/component/application/test_comparison.py` | `COMPARED`（合成、ヤング率変更のみ、自動1・手動1） | 型付き差分は実装済・実証範囲は限定 | `CasePatch` と `apply_patch` は材料だけでなく支持・治具・運動・接触・メッシュ・出力等も受理する。これを全変更のnative再生成・再解析・比較が実証済みとは扱わない。現行受入証拠はE-only＋mesh再利用に限る |
| 12 | 日本語入力・LLM（AI-01／AI-02、既存契約） | `adapters/llm/openai_responses.py`、`application/_intent.py`、`_intent_grounding.py` | `tests/component/autonomy`（模擬）、`tests/native/test_llm.py`（`llm` マーカー、実provider） | **未取得**（実LLMを用いた受入記録が現行に無い） | 実装済・証拠未取得 | 承認済みLLM設定（provider／model／key_env／予算）が未提供 |
| 13 | 結果の簡潔な自然言語報告 | 該当実装を確認できず | なし | 未取得 | 未着手 | 開発契約の目的およびロードマップに含まれる。設計仕様書 §8 側の文言整備が要る |
| 14 | 新規環境への通常wheel配布 | `pyproject.toml`、`tests/e2e/test_installed_synthetic.py` | E2E-01（`e2e` マーカー、1関数） | 開発機の新規環境で22コマンド全exit 0 | 実装済 | 開発機以外での再現は現行受入証拠 未取得 |
| 15 | 中断・復旧・所有権 | `cli/run.py`（`status`／`resume`／`cancel`）、`storage/_ownership.py`、`application/_run_reconciliation.py` | `tests/component/application/test_run_cancellation.py`、`test_run_reconciliation.py` | 合成のみ | 部分実装 | `resume` はヘルプ上「中断した同期公開を**再開せずに診断する**」であり、実行の再開機能ではない。実ツールでの復旧証拠は未取得 |
| 16 | 予算管理 | `domain/budget.py`、`storage/demo_budget.py`、実装ノート §3・§8 | `tests/unit/contracts/test_budget.py`、`tests/component/storage/test_demo_budget.py` | 予約台帳による会計（合成flow・手動flow） | 実装済 | 予算超過時の挙動、LLM予算の実地証拠は未取得 |
| 17 | 記録保護（実CAE・資格情報のGit混入防止） | `cli/scan_cae_data.py`、`.gitignore`、`storage/*` | `tests/unit/test_scan_cae_data.py`、`tests/component/storage` | 初稿記録はPASS（280 checked／280 tracked／0 diagnostics、exit 0）。本改訂の検査はreadiness記録参照 | 実装済 | scannerはGit混入検知であり実モデル操作の許可発行機構ではない。登録ケース境界・リンク検査と、モデルごとの使用許可を区別し、許可領域での実E2E証拠を取得する |
| 18 | 実モデルE2E（E2E-02） | — | **`tests/e2e/test_authorized_real_case.py` が存在しない** | 未取得 | 未着手 | 計画書 §7 が実行entrypointとして挙げるファイルが未作成。E2E設定は `scope="synthetic_explicit"` のみ受理（`tests/e2e/test_installed_synthetic.py:1055`） |
| 19 | 最終BottomFrame E2E（E2E-03） | — | **`tests/e2e/test_bottomframe_final.py` が存在しない** | 未取得 | 未着手 | 同上。加えてBottomFrameの正式入力・許可領域・評価条件が未提供（設計仕様書 §10） |
| 20 | 実ツール試験の入口 | `pyproject.toml` のマーカー定義 | `tests/native` は `native` 2件・`llm` 1件 | — | 部分実装 | 計画書 §7 が挙げる `pytest tests/native -m "febio"` に該当する試験が現行リポジトリに0件 |

### 2.1 不足の型

1. **既存経路の実証が未取得**：#7 メッシュ依存性の算定、#12 実LLMの材料intent/answer/edit、#14 別環境配布。→ 許可と有限予算のもとで既存経路から着手する。製品全体の6項目必須化・日本語CAD全経路・結果報告まで無変更で通るとは保証しない。
2. **判定の成立条件が未整理**：#8 物理適用性（実物照合が要る）、#3 surface approximation（双方向境界が未確立）、#2 native qualification（対応資格の証拠不足と解消手順）。
3. **公開インターフェースの不足**：#10 `CONFIRMED` は小さな公開ブリッジで補完（実UI未検証）。#13 結果報告、#18／#19 実モデルE2Eの試験ファイルと設定scopeは未実装。
4. **既存契約に対して実装が限定的**：#11 変更種別、#4 対応表の能力範囲（球・円柱・摩擦・Neo-Hookean）、#15 復旧。

### 2.2 MVP判定と製品判定の差分

`application/_required_quality.py:75-124` はglobal `mesh_dependence=UNVERIFIED` を許容して `PASS` を返し、`tests/component/febio/test_required_quality_status.py:124-163` がそのMVP契約を固定する。`application/_preview.py:73-103` も `LAUNCHED` と `CONFIRMED` の双方で `COMPLETE` を許容する。したがってM2で実証を取得するだけで製品の完了判定が自動的に厳格化するわけではない。**次の最小変更候補**は、既存MVP記録の意味を保持したまま、製品受入側で6項目の根拠ある合格と `CONFIRMED` を確認することである。製品公開判定の切替箇所をM2で確定し、影響する既存の品質・preview試験を更新する。判定を手動上書きしたり `UNVERIFIED` を隠したりしない。

## 3. ロードマップ

順序は **M1 → M2 → M3 → M4 → M5**（ユーザー指示・**確定**）。本書は各milestoneの着手時期を提案するだけであり、§1.2 の既存契約を削除・縮小しない。

### M1：製品範囲と受入基準の合意

| 項目 | 内容 |
|---|---|
| 目的（確定） | M2以降の作業について「何をもって合格か」を先に固定し、証拠の取り直しを防ぐ |
| 成果物（確定） | 本書と[M1 readiness記録](../reviews/2026-09-23-product-m1-readiness.md) |
| 範囲外（確定） | `src`／`tests` の変更、新機能、実ツール実行 |

**exit criteria は2段階に分ける（提案）。**

| 段階 | 条件 | 現在地 |
|---|---|---|
| M1-A：準備完了 | ①対応表（§2）が現行コードの実読取に基づく ②M2〜M5のexit criteria草案がある ③既存契約／提案／ASKが分離されている ④依存関係と予算の予備見積がある ⑤docsのみのcommitで作業ツリーに差分が無い ⑥`git diff --check` と `scripts/scan_cae_data.py` がPASS | **本task完了時点で達成**（実測値は readiness記録に記載） |
| M1-B：段階合意 | M2予算とM3条件を分離して記録する | **M2予算合意済み・M3条件待ち**。M2は着手可能、M3は `ASK_AND_BLOCK`。全体合意ではない |

### M2：既存契約のうち平面・直方体で取れる証拠の取得

| 項目 | 内容 |
|---|---|
| 目的（確定） | 既存契約の品質6項目目・`CONFIRMED`・形状品質について、いま取得できる証拠を取る |
| 着手範囲（確定） | **平面・直方体のM2から着手可能**（承認済み予算は§5）。球・円柱・摩擦・圧縮性Neo-Hookean・変更種別の拡張は §1.2 の既存契約に残し、製品版完了までの残件として扱う（**範囲から外さない**） |

対象は3系統ある。

**(a) Studio `CONFIRMED`**

- 位置づけ（既存契約）：`CONFIRMED` は設計仕様書 §7 の要件として確定済みであり、再承認は不要（2026-09-23 PM回答）。独立観測の要件は維持する。
- 現状（確定）：`--window-id` ＋ stdin PIPEの観測経路と `PreviewReceipt` の `CONFIRMED` 検証は実装済み。公開ブリッジ `scripts/observe_preview.py` がinstalled CLIの実要求を外部交換ディレクトリへ公開し、独立操作者の応答1件をそのままPIPEへ渡す（実装ノート §7）。
- 準備実装：表示値の自動転記・PNG生成・`CONFIRMED` の上書きは行わず、既存検証に委ねる。既存の `LAUNCHED` 契約は変更しない。準備の検証記録は[M2 preview preparation](../reviews/2026-09-23-product-m2-preview-preparation.md)を参照。
- exit criteria（提案）：実Studio 1回の起動に対して `preview_status=CONFIRMED` となり、記録に観測主体・一回限りの識別子・PNGハッシュ・表示変数と最終状態が残る。PNG自体はGit外に保持する。
- **観測主体（確定）**：PM。比較完了前の公開preview要求が所有ロックbusyでexit 8となり、Studio起動前に停止した。比較はその後COMPARED／exit 0で完了・所有権解放済み。Studio実起動0・予約1を保持し、同一予約での続行判断待ち。実UI／PNG／CONFIRMEDは未取得。手順は実装ノート §7、事実と原因は[M2実測記録](../reviews/2026-09-23-product-m2-evidence.md)を参照。

**(b) メッシュ依存性の3段階**

- 現状（確定）：新M2 flowの3段階とE-only子で実証済み。3版PREPARED・4run SUCCEEDED、fine／childの数値6項目PASS。要素数325→889→1966、最大相対力差0.0007783796064404918、材料正規化差0。[M2実測記録](../reviews/2026-09-23-product-m2-evidence.md)に全コマンド・失敗修正・有限予算を記録した。
- 実施方法（実施済み）：既存の `prepare-planar --parent-revision-id` で粗→中→細を作り、実FEBioで各版を解析した。受理済みinstalled wheelを変更せず、公開CLIから合成平面・直方体の証拠を取得した。他形状・実モデルへはこの合格を一般化しない。
- exit criteria（提案）：同一ケースで粗・中・細の3版が `PREPARED`、3版とも実FEBioで `SUCCEEDED`、部品要素数の増加と実測最大辺長の減少が記録され、双方の全保存状態の力履歴差が事前宣言した相対力差限界と力下限を満たし `mesh_dependence=PASS` となる。4回目はヤング率のみを変更し、同一最細メッシュと弾性率正規化比較による継承も `PASS` とする（計画書 §5）。`FAIL`／`UNVERIFIED` は調査結果として保持するが、この受入条件の合格とはしない。許容値を後から緩めない。

**(c) 形状品質と対応資格（`surface_approximation`／`native_qualification`）**

- 方針（確定、2026-09-23 PM回答）：物理的な実物照合の状態は既存どおり数値品質とは別行に表示し、未検証なら `UNVERIFIED` のまま残す。`surface_approximation` の適用範囲は既存契約と数値証拠に従い、**一律の必須化はしない**。
- `surface_approximation`（提案）：直方体治具・平面部品に限り、生成メッシュ面とCAD面の**双方向**の符号付き距離境界が成立するかを調査する。成立する範囲でのみ `PASS` を主張し、成立しない範囲は `UNVERIFIED` のまま残す。既存の区間算術（`adapters/meshing/native_surface.py`）の再利用可否も調査対象とする。実Gmsh・実FEBio予算は消費しない。
- `native_qualification`（提案）：**既存契約を維持する。**恒久的な `UNVERIFIED` 化も、新しいハッシュ束縛や権限レイヤーの追加も、本taskおよびM2では採用しない。M2では「対応資格を満たすために何の証拠が不足しているか」と「その解消手順」を調査して記録することまでを行う。
- `physical_applicability_validation`（既存契約）：別行表示のまま維持する。**新しい必須品質項目は追加しない。**解消には実測との照合が要るため、M2では証拠不足の内容を記録するにとどめる。
- exit criteria（提案）：3項目それぞれについて、成立する主張・成立しない理由・解消に必要な証拠が記録され、`PASS` にできない項目は理由付きで `UNVERIFIED` として公開される。
- 調査結果：既存算定だけでは双方向CAD↔meshの被覆・距離境界が未成立。資格・形状近似・実物適用性の不足証拠を[M2実測記録](../reviews/2026-09-23-product-m2-evidence.md)に記録し、3項目のUNVERIFIEDを維持した。調査に伴うnative起動・製品コード変更は0。
- 必要形状の補完（提案）：M3の確定入力を既存対応表へ照合し、未対応の治具・配置・接触・材料だけをM2で補完し、その組み合わせの参照解・実ツール証拠を取得する。対象未確定の間に球・円柱等の全組み合わせを作らない。既存製品契約の残件は削除せず、未認定の組み合わせではM3を開始しない。

### M3：実モデルE2E（E2E-02 → E2E-03）

| 項目 | 内容 |
|---|---|
| 目的（確定） | 許可された実STEPで全経路を通し（E2E-02）、続いて最終BottomFrame実モデルで同じ全経路と指定品質を通す（E2E-03） |
| 前提（確定） | 対象CAD、ケース領域、許可された操作、物理条件、評価基準の確定（計画書 §7）。実 `02_CAE` への書込は最終工程の**新規実行領域のみ**に限定し、元データを保持する |

- 不足（確定）：`tests/e2e/test_authorized_real_case.py` と `tests/e2e/test_bottomframe_final.py` が存在しない。E2E設定は `scope="synthetic_explicit"` しか受理しない。
- 必要な変更（提案）：E2E設定に実モデル用のscopeを追加し、対象STEP・許可書込先・ケース領域・物理条件・評価基準・予算を型付きで受理する。試験ファイルは合成E2Eの構造を流用し、**合成STEP固有の前提（体積・面数の期待値など）を実モデルへ持ち込まない**。
- exit criteria（提案、モデル1件あたり）：STEP調査 → 根拠付き条件確定 → 準備・版固定 → 実FEBio `SUCCEEDED` → メッシュ依存性を含む必須6項目が事前基準を満たす → Studio `CONFIRMED` → 1項目以上の編集 → 新規解析 → 品質・表示確認 → 比較、までが公開CLIで通る。各出力の対象版・manifestを記録し、親の品質・previewを子へ無条件に流用しない。`docs/reviews/` に対象SHA・変更ファイル・実コマンドと終了コード・実FEBio／Studio起動回数・未検証事項・次判断を残す。実データ・実パスはGitに入れない。
- E2E-03の位置（確定）：E2E-02の後、**M3の中**で実施する。BottomFrameの正式入力・条件が揃わない場合は**未実施のまま残す**（計画書 §7）。後続milestoneへ移動させない。
- **ケース固有条件は未提供である**（§8.3 Q3〜Q5）。材料・支持・押し込み方向と量・接触対・摩擦・評価領域・許容差・評価基準はいずれも未確定であり、本書はこれらの値や枠を推測しない。

### M4：実LLMによるAI-02（日本語→質問→変更→結果報告）

| 項目 | 内容 |
|---|---|
| 目的（確定） | 日本語でCAD解析条件を与え、不足は質問で解消し、条件変更を適用し、**結果を簡潔な自然言語で報告する**までを実LLMで通す。AI-02の3操作も結果報告も、既存の契約・目的に含まれる（2026-09-23 PM回答。要否の再承認は不要） |

- 現状（確定）：`tests/native/test_llm.py` は実provider向けの `llm` 試験で、日本語の材料 `intent` → `NEEDS_INPUT`（終了コード3）→ `answer` → 明示 `validate/freeze` → E-only `edit` を確認する。既存合成fixtureへサービスを差し替えてCLI関数を呼ぶ試験であり、新規CADのinstalled CLI全経路・実FEBio・Studio・自然言語結果報告はこの試験に含まれない。3操作の計測＋生成は計6呼出を期待する。現行受入記録に実LLM合格証拠は無い。
- 不足（確定）：承認済みLLM設定が未提供。結果の自然言語報告について、該当実装と設計仕様書の契約行を確認できていない。
- 必要な変更（提案）：比較・品質・実行の登録済み記録だけを入力として日本語の要約文を返す、読み取り専用の報告経路を用意する。LLMには形状要約・明示条件・未解決事項・差分・集計値・関連ログのみを渡し（設計仕様書 §8）、提案に実行権限は与えない。設計仕様書 §8 の文言整備はM4着手時に行う。
- exit criteria（提案）：承認済みの新規ケースで、日本語CAD条件入力 → 不足への質問 → 回答 → 明示的な検証・版確定 → 解析 → 日本語変更の型付き適用 → 再検証・子版確定・再解析・比較 → 結果の簡潔な日本語報告、を接続して実証する。LLM提案だけでfreeze/runしない。報告は対象版・単位・比較軸・品質・未検証理由に一致し、失敗・未完了を成功と記述しない。AI-02試験単独のPASSをこの全経路の代用にせず、実LLM呼出回数・トークン使用量・操作IDと解析側証拠を別々に記録する。送信データ範囲も承認し、鍵・CAD全体は送信しない。
- **未確定（M4着手前）**：provider／model／key_env／有限予算（§8.2 Q6後半）。これが揃うまで実LLMは呼び出さない。

### M5：新規環境配布・復旧・予算・記録保護・限定利用の受け入れ

| 項目 | 内容 |
|---|---|
| 目的（確定＋提案） | 開発機以外でも通常インストールから動作し、異常時に安全に止まり、予算と記録保護が働き、限定された利用者が受け入れられる状態にする |

- 現状（確定）：新規環境インストールからのE2E-01は開発機で成功。開発機以外での再現、実ツールでの復旧、予算超過の実地証拠はいずれも現行受入記録に無い。`resume` は再開ではなく診断である。
- exit criteria（提案）：
  1. **配布**：開発機とは別の新規Python 3.12環境へ同一wheelを通常インストールし、8手順が再現する（wheelのSHA-256一致を記録）。
  2. **復旧**：実行中に所有プロセスを強制終了させ、`status`／`resume` が中断を正しく報告し、結果を完成として公開しないことを実ツールで確認する。
  3. **予算**：宣言した上限を超える要求が起動前に拒否され、失敗・中断でも消費枠が戻らないことを記録で確認する。
  4. **記録保護**：`scripts/scan_cae_data.py` がPASSし、実CAE・資格情報・ローカルパスがGitに無いことを候補コミットで確認する。`02_CAE` への書込がM3の許可領域に限られていることを記録で示す。
  5. **限定利用受入**：利用者・対象機・利用範囲・受入証拠の定義に従って受け入れ記録を残す。この定義は**提案のまま保持し、M5着手前に合意する**（§8.2 Q7）。
- M5は製品版完成の宣言ではない。§1.2 の既存契約のうち未取得の項目（球・円柱・摩擦・Neo-Hookean・変更種別の拡張など）は、完了判定の対象として残る。

## 4. 依存関係

```text
M1(M2予算合意済み・M3条件待ち) ──► M2(平面での証拠取得) ──► M3(実モデル E2E-02 → E2E-03) ──► M4(実LLM) ──► M5
   |             |                         |                              |              |
   |             |                         |                              |              +— Q7(限定利用の定義)をM5前に合意
   |             +— (b)3段階meshと(a)Studioの予算は承認済み               +— Q6後半(LLM設定)が無いと着手不可
   +— Q3/Q4/Q5(対象・使用許可・物理条件・評価基準)が無いと M3 は着手不可 ----+
```

- **確定**：実施順序は M1 → M2 → M3 → M4 → M5。予算を絞る場合でも順序は変えず、M2を終えないままM3へ進めない。E2E-03はM3の中に置く。
- **確定**：M5は先行milestoneの記録を束ねる位置にあるため、それらが揃うまで受入宣言はできない。
- **確定**：§8.3 の未回答は該当milestoneだけを止める。M1の文書準備と、他milestoneの設計・調査は止めない。

## 5. 実ツール実行予算（M2承認済み・後続は予備見積）

以前のnative予算は消費済みであり、**流用しない**。

2026-09-23の承認 `msg_757df3e48e7a`：**M2 inspection 1／preparation 3／実FEBio 4、別枠Studio起動1**。旧caseは保持し、失敗時停止・再試行0。この同一予算の再承認は求めない。M3以降の表中予算は提案のままであり、判断委任は未指定の物理条件を推測する許可ではない。

**M2の行は上記承認済み、M3以降は予備見積であり、製品版全体の確定総量ではない。** §1.2 の既存契約のうち、球・円柱治具、圧縮性Neo-Hookean、Coulomb摩擦、変更種別の拡張、対応表の能力拡張、対応資格（native qualification）の証拠取得に必要な検証は**含まれていない**。これらは範囲に残っており、必要量は対象と基準が決まってから別途見積もる。

| milestone | 内訳 | 調査（inspect） | メッシュ生成（preparation） | 実FEBio | Studio起動 | 実LLM呼出 |
|---|---|---:|---:|---:|---:|---:|
| M1 | 文書のみ | 0 | 0 | 0 | 0 | 0 |
| M2 (a) Studio `CONFIRMED` | 既存manifestの再表示1回 | 0 | 0 | 0 | 1 | 0 |
| M2 (b) 3段階mesh依存 | 新flow 1本（粗・中・細＋ヤング率変更はメッシュ再利用） | 1 | 3 | 4 | 0 | 0 |
| M2 (c) 形状品質・対応資格 | 解析的調査のみ | 0 | 0 | 0 | 0 | 0 |
| M3 E2E-02（実モデル1件） | 単一mesh・解析2回という下限候補。6項目品質・子版表示を満たす確定予算ではない | 1 | 1 | 2 | 1 | 0 |
| M3 E2E-03（BottomFrame） | 同上。各モデルの収束調査・変更内容で再見積 | 1 | 1 | 2 | 1 | 0 |
| M4 AI-02 | 材料3操作の計測＋生成6呼出、報告分2呼出の予備枠。CAD全経路の解析側予算は未算入 | 0 | 0 | 0 | 0 | 最大8 |
| M5 | 別環境の8手順1回＋復旧試験という下限候補。製品判定の追加実証分は未算入 | 1 | 1 | 3 | 1 | 0 |
| **予備見積 合計** | **（上記の範囲のみ）** | **4** | **6** | **11** | **4** | **8** |

運用条件（M2は承認済み、後続milestoneは提案）：

- **M2(b)の承認上限は inspection 1／preparation 3／実FEBio 4、失敗時は停止し再試行0とする。** 失敗・中断も消費として数え、復元しない（実装ノート §3 の既存運用）。
- 時間上限は既存設定を根拠に明記する。`inspect --native` は既定600秒（`cli/main.py` の `--wall-seconds` 既定、実装ノート §2 の上限）、`prepare-planar` は既定600秒・生成1回・四面体100,000要素・節点250,000（実装ノート §3）。M2実行前のprelaunchで、実FEBioは固定した `values.budget.max_elapsed` に基づく各3600秒・CPU 1、preparationは各570秒と確定した（M2実測記録）。後続milestoneの上限は別途確定する。
- `--preflight` はネイティブソルバーを起動しないため消費しない（manual 8記録 §5）。
- M2(a)は新M2 flowのmanifestを使う案を優先する。保護対象の既存case操作を暗黙に許可した予算ではない。M3でモデル固有の3段階mesh＋E-onlyを要する場合、各モデル最低 `inspect 1／prep 3／FEBio 4` に再見積し、必要な親子表示の起動回数も追加する。M4の新規CAD→解析→変更→再解析には別途native予算を積む。上表の合計だけを全milestoneの実行許可として扱わない。

## 6. 各milestoneの最小構成（順序は変えない）

予算を段階的に承認する場合の**最小構成**を提案する。承認はmilestone単位で分けてよいが、**実施順序は M2 → M3 → M4 → M5 のまま変えない**。M2を終えないままM3へ進めず、E2E-03をM3の外へ移動させない。

| 順 | milestone | 最小構成 | 必要予算（予備見積） | この最小構成に含めないもの |
|---:|---|---|---|---|
| 1 | M2 | (b) 3段階mesh依存を1 flow、続けて (a) `CONFIRMED` 1回、(c) は解析的調査のみ | inspect 1／prep 3／FEBio 4／Studio 1 | 球・円柱・摩擦・Neo-Hookean（既存契約に残件として保持） |
| 2 | M3 | E2E-02を実モデル1件、続けてE2E-03（正式入力が揃った場合） | inspect 2／prep 2／FEBio 4／Studio 2 | 追加モデル、変更種別の拡張 |
| 3 | M4 | AI-02を1 flow（合成ケースで可）＋結果の日本語報告 | LLM 最大8 | 複数モデル・複数providerの比較 |
| 4 | M5 | 別環境1台での8手順再現＋復旧・予算・記録保護の各1件 | inspect 1／prep 1／FEBio 3／Studio 1 | 3台目以降の環境、長期運用試験 |

**現在の実施判断**：M2(b)は合格、(c)調査済み、(a)は起動前競合拒否後の続行判断待ち（[M2実測記録](../reviews/2026-09-23-product-m2-evidence.md)）。native inspect1／prep3／FEBio4を消費し、Studioは実起動0・予約1保持。M3はQ3〜Q5と固有予算待ちであり、M2完了と条件確定前には進まない。

## 7. 各milestoneで触らないもの（確定）

- 保護ブランチ `native-curved-primitives`／`acceptance-gate-repairs`、およびsoft-holdの作業ツリーは削除しない。
- 旧manual `case-6294a0a9e02c`（preparation `FAILED`／`ABORTED/BLOCKED`）と受理済みautomated `case-80e5f42a5043`、manual final `case-bb9e975f3fb3` は、retry／reset／代替case／証拠の付け替えを行わない。
- `adapters/geometry/_gmsh_runtime.py`（依存関係認証）は正常動作している限り触らない。
- 新たなハッシュ固定・自己ハッシュ・追加の権限レイヤーは導入しない。
- 既存契約の範囲（§1.2）を、本書の提案によって削除・縮小しない。
- V2への統合と `git push` はPMが担当する。
- 開発運用の永久ルール：Codex週間使用率 `used >= 60%` でCodexの新規継続を停止し、Opus／Geminiは利用可能な範囲で継続できる。リセット検知後は重複しない自動再開とstop/resumeのRun inbox報告を行う。これは製品の解析予算とは別のPM担当機構であり、本M1では実装・監視設定を変更しない。

## 8. 質問と回答の状態（2026-09-23 PM回答を反映）

一括承認（「全部合意案どおり」）は、ケース固有条件の未指定を解消できないため選択肢から撤去した。

### 8.1 回答済み（確定）

| # | 質問 | 回答（2026-09-23、PM経由） |
|---:|---|---|
| Q1 | `physical_applicability_validation` と `surface_approximation` の扱い | **既存仕様どおり。**物理的な実物照合の状態は数値品質とは別行に表示し、未検証なら `UNVERIFIED` のまま残す（設計仕様書 §1・§6）。`surface_approximation` の適用範囲は既存契約と数値証拠に従い、一律の必須化はしない。**適用根拠の登録を新しい必須品質項目として追加しない。** |
| Q2 | Studio `CONFIRMED` の必須性 | **既存の製品契約で確定済み**であり再承認は不要（設計仕様書 §7）。独立観測の要件は維持し、観測主体はPMと確定した |
| Q6前半 | 「結果の簡潔な自然言語報告」の要否 | ロードマップおよびユーザーの目的に含まれるため、新たな要否承認は不要。設計仕様書側の文言整備はM4着手時に行う |
| 範囲の扱い | 設計仕様書 §1「目標範囲」の再承認／縮小 | 既定の製品契約として既にあるため、未確定扱いにして再承認を求めない。今回勝手に狭めない。初回リリースへ含める順序・範囲の提案は契約との差分として扱う（§1.2） |
| Q2b | 観測主体と手順 | PMによる実desktop UIの独立確認、要求後のPNG、公開ブリッジでの期限内応答（実装ノート §7）。実観測は未実施 |
| Q8（M2分） | 新規実ツール実行予算 | `msg_757df3e48e7a` でinspection 1／preparation 3／実FEBio 4＋別枠Studio 1を承認。旧case保持、失敗停止、再試行0 |

### 8.2 該当milestone直前に合意する項目（現時点では提案のまま）

| # | 項目 | いつ確定するか | 提案 |
|---:|---|---|---|
| Q6後半 | 実LLMの provider／model／key_env／有限予算 | **M4着手前**（現時点では未確定事項） | `FEBIO_CAE_NATIVE_LLM_SETTINGS` で承認済み設定を提供してもらう。モデルの自動代替はせず、鍵は引数・ファイル・ログ・エラーへ出さない（実装ノート §8） |
| Q7 | M5「限定利用」の定義（利用者・対象機・受入証拠） | **M5着手前** | 利用者・対象機・利用範囲・受入証拠を明示し、新規環境での通常インストールと8手順の再現、復旧・予算・記録保護の各1件をもって受け入れとする |

### 8.3 未回答（ASK_AND_BLOCK、PMから開発部長へ照会中）

**これらが返るまで、依存する作業は実行しない。推測で埋めない。**

| # | 質問 | 影響するmilestone | 状態 |
|---:|---|---|---|
| Q3 | E2E-02の対象実STEPはどれか。書込を許可する領域はどこか。部品と治具の役割はどう割り当てるか | M3 | 未回答。対象・許可が確定するまでケースを作らない |
| Q4 | E2E-02／03の物理条件：材料（ヤング率・ポアソン比・出典）、支持面、押し込み方向と量、接触対、摩擦、評価領域、許容差 | M3 | 未回答。**ケース固有条件であり推測しない。**型付きJSONでの提供を依頼している。既存のMVP条件と同じ枠に収まるという想定も置かない |
| Q5 | 実モデルでの評価基準（何をもって合格とするか）と許容値 | M3、M5 | 未回答。参照値・判定方法・許容値はいずれも未提供。本書は代替案を確定として置かない |
| Q8（後続分） | M3〜M5の実ツール予算 | M3〜M5 | 提案のまま。M2分は§8.1で承認済みであり再照会しない。以前のnative予算は流用しない |

## 9. M1-Bの現在地と残る合意

**M1-B＝M2予算合意済み・M3条件待ち**。M2は承認済み有限予算で着手可能だが、製品全体またはM1全体の合意完了ではない。

- M3はQ3〜Q5（実対象・使用許可・物理条件・評価基準）が揃うまで `ASK_AND_BLOCK`。実モデル操作は行わず、不足を推測しない。
- M3〜M5の予算は将来の提案であり、M2承認や判断委任から拡張しない。
- M2の実UI・数値証拠と完了判定は別に記録する。ブリッジ準備のみでM2完了を宣言しない。
- **履歴**：初稿とM1-A仕上げ時点ではQ3〜Q5・Q8が未回答で `M1_PREPARATION_COMPLETE_AGREEMENT_PENDING` だった。今回のM2予算承認でその一部が解消されたが、過去記録の合意待ちを当時の履歴として保持する。
