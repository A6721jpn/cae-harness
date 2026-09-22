# FEBio CAEハーネス — 製品版ロードマップ M1〜M5（合意用草案）

作成日：2026-09-23／状態：**提案（合意未完了）**。本書はM1「製品範囲と受入基準の合意」の調査成果である。2026-09-23のPM回答で §8.1 の3件は確定したが、§8.3 の未回答（実モデルの対象・許可・物理条件・精度基準・新規予算）が残るため、**合意完了は宣言しない**。
ファイル名の日付は識別子である。参照順序は[開発契約](../../AGENTS.md) → [計画書](2026-09-14-febio-cae-harness-greenfield-plan.md) → [設計仕様書](../specs/2026-09-14-febio-llm-cae-harness-design-v2.md) → [実装ノート](../specs/implementation-notes.md) → [canonical review](../reviews/2026-09-20-mvp-planar-e2e.md)・[manual 8実行記録](../reviews/2026-09-22-mvp-manual-final.md)。本書とこれらが食い違う場合は既存文書を正とする。

本書の対象コミットは `67cb7fe`（ブランチ `orca/acceptance-integration`）。本書の作成にあたって `src`／`tests` は変更しておらず、実FEBio・実FEBio Studio・実LLM・実CAEモデル（`02_CAE`）への操作は0である。

## 0. 表記と読み方

| 表記 | 意味 |
|---|---|
| **確定** | 既存の設計仕様書・計画書・開発契約・受入記録に根拠があり、本書はそれを写しているだけ |
| **提案** | 本書で新たに提案する内容。ユーザーが承認するまで製品契約ではない |
| **ASK_AND_BLOCK** | 記録から確定できず推測もしない項目。回答がないと当該作業を開始できない |

状態語は開発契約の規則に従う。`INSPECTED`＝観測完了、`PROVISIONED`＝登録完了、`PREPARED`＝準備記録の公開、`SUCCEEDED`＝終端到達と出力完全性、`LAUNCHED`＝表示ソフトの起動、`CONFIRMED`＝表示内容の独立観測、`COMPLETE`＝品質・表示を含む完了。合成データ・模擬ログの合格を、実FEBio・実Studio・実モデルの成功証拠に読み替えない。証拠不足は `UNVERIFIED` のまま残す。

Git外の証拠rootは記号で参照する（`<COORDINATION>`＝元のチェックアウト/.local/coordination、`<ROOT_PYTEST>`＝元のチェックアウト/.local/pytest-basetemp）。実パス・資格情報・実CAEデータは本書に書かない。

## 1. 現在地（すべて確定）

- MVP（計画書 §2 の8手順）は完了している。自動flowは[canonical review](../reviews/2026-09-20-mvp-planar-e2e.md)（`case-80e5f42a5043`、22 stage全exit 0）、手動flowは[manual 8実行記録](../reviews/2026-09-22-mvp-manual-final.md)（`case-bb9e975f3fb3`、公開CLI 22コマンド全exit 0）を正とする。
- 現行の版は source=`71c388f`、test-only=`56e122b`、format-only=`0e35ba4d`、docs head=`67cb7fe`。native final wheel SHA-256=`f978de60803f70b9a5858a60bae185e0ed467949b0e062eafd2826a5e18b2cdd`／size=`404193`。
- 最終標準full-suiteは `1798 passed / 0 failed / 3493.86 s`（`56e122b`、exit 0）。
- **製品版（最終像＝CADと解析条件を与えるだけでLLMが解析し、結果を自然言語で報告する）は未完了である。** 残っている主な境界は、実モデル受け入れ（P7）、日本語・実LLM経路（P4／AI-02）、メッシュ依存性を含む品質6項目目、Studio `CONFIRMED`、新規環境での配布・復旧・限定利用の受け入れである。
- MVPの達成は最終project完成を意味しない（計画書 §2、manual 8記録 §6）。

## 2. 現状の対応表（製品能力 ↔ コード ↔ 試験 ↔ 実証拠）

静的な規模（2026-09-23、read-only計数）：`src` 106ファイル／約40,975行、`tests` 約40,041行。試験関数の定義数は unit 455、component 493、e2e 1、native 3。実行時の試験件数（parametrize展開後）は最終full-suiteの `1798` であり、関数定義数と混同しない。

| # | 製品能力 | コード（正） | 試験 | 実証拠（実ツール／実モデル） | 状態 | 具体的な不足 |
|---:|---|---|---|---|---|---|
| 1 | ケース登録・草案世代・不変版・原子的保存 | `domain/*`、`application/service.py`、`storage/*` | `tests/unit/contracts`、`tests/component/storage` | 合成のみ | 実装済 | 実モデルでの適用実績0 |
| 2 | STEP調査（`inspect --native`） | `adapters/geometry/inspection.py`、`_gmsh_runtime.py` | `tests/component/geometry` | 実Gmsh 4.15.2で `INSPECTED`（合成STEP、自動1・手動1） | 実装済 | `native_qualification` は `application/_inspection.py:84` で `UNVERIFIED` 固定。実機適合の認定処理は無い |
| 3 | 平面準備（Tet10部品＋直方体剛体） | `adapters/geometry/preparation.py`、`adapters/geometry/adapter.py` | `tests/component/geometry`、`tests/component/application/test_planar_preparation.py` | 実Gmshで `PREPARED`（合成、自動1・手動1） | 実装済 | `surface_approximation` は `application/_preparation.py:453` で `UNVERIFIED` 固定。`adapters/geometry/adapter.py:1068` の `surface-approximation` レコードも常に `UNVERIFIED`（理由＝双方向のCAD↔メッシュ境界が未確立） |
| 4 | 対応表（組み込み既定バンドル） | `resources/planar_default_bundle.json`、`application/_profile_provisioning.py` | `tests/component/febio/test_profile_*.py` | `PROVISIONED`（合成、自動1・手動1） | 実装済 | 能力範囲は `febio.scope.planar_linear_frictionless_fixed_xyz` のみ。球・円柱・摩擦・非線形材料の対応表は無い |
| 5 | 入力生成・所有プロセス実行・出力固定 | `adapters/febio/compiler.py`、`runner.py`、`_windows_job.py` | `tests/component/febio` | 実FEBio 4.12.0で `SUCCEEDED`（合成、自動2・手動2） | 実装済 | 実モデルでの実行0 |
| 6 | 必須数値品質5項目 | `adapters/febio/quality.py`、`planar_quality.py`、`reported_norms.py`、`application/_required_quality.py` | `tests/component/febio/test_required_quality_status.py` ほか | 必須5項目 `PASS`（合成、自動1・手動1） | 実装済 | 実モデルでの判定0。参照解は計画書 §5 の弾性パッチのみ |
| 7 | メッシュ依存性 `mesh_dependence` | `application/_mesh_refinement.py`（3段階判定・局所球版を実装） | `tests/component/febio/test_mesh_refinement.py`、`tests/component/geometry/test_native_local_refinement.py` | **未取得**（3段階の実行が1度も無い） | 実装済・証拠なし | 粗・中・細3版の実FEBio結果が必要（メッシュ生成3回・FEBio実行4回）。E2E設定は `preparation_requests` 3件を既に受け付ける |
| 8 | 物理適用性 `physical_applicability_validation` | **判定処理なし**。`application/_required_quality.py:327` が理由付き `UNVERIFIED` を生成するのみ | `tests/component/application/test_required_numerical_quality.py:151`（`UNVERIFIED` であることの確認） | なし | 未実装 | 合格の定義そのものが未確定（§8 Q1） |
| 9 | Studio表示 `LAUNCHED` | `adapters/preview/studio.py`、`application/_preview.py` | `tests/component/febio/test_preview_binding.py` ほか | 実Studioで `LAUNCHED`／`task COMPLETE`（合成、自動1・手動1） | 実装済 | 表示内容は `UNVERIFIED`。receiptの `version` も `UNVERIFIED` |
| 10 | Studio表示 `CONFIRMED` | `cli/preview.py`（`--window-id` ＋ stdin PIPE）、`domain/preview.py:166`、`application/_preview.py` | `tests/component/febio/test_preview_existing_session.py` ほか | なし | 経路のみ実装済 | 観測要求を受けてPNGを返す**公開ヘルパーが現行CLIに無い**（実装ノート §7）。観測者・PNG・照合手順が未整備 |
| 11 | 部分変更と比較 | `domain/case_patch.py`、`application/_comparison.py`、`cli/compare.py` | `tests/component/application/test_comparison.py` | `COMPARED`（合成、ヤング率変更のみ、自動1・手動1） | 部分実装 | 実装済みの変更はヤング率置換とメッシュ再利用のみ。支持・接触・運動・治具形状の変更経路は未実施 |
| 12 | 日本語入力・LLM（AI-01／AI-02） | `adapters/llm/openai_responses.py`、`application/_intent.py`、`_intent_grounding.py` | `tests/component/autonomy`（模擬）、`tests/native/test_llm.py`（`llm` マーカー、実provider） | **なし（実LLM呼出0）** | 実装済・証拠なし | 承認済みLLM設定（provider／model／key_env／予算）が未提供。受理文法は実装ノート §8 の `field = value` 形式に限定 |
| 13 | 結果の自然言語報告 | **実装なし** | なし | なし | 未着手 | 最終像（開発契約の目的）の構成要素だが、設計仕様書に契約行が無い。追加には仕様改訂が必要（§8 Q6） |
| 14 | 新規環境への通常wheel配布 | `pyproject.toml`、`tests/e2e/test_installed_synthetic.py` | E2E-01（`e2e` マーカー、1関数） | 開発機の新規環境で22コマンド全exit 0 | 実装済 | **別の機械／別のユーザー環境での再現は0**。配布物はローカルwheelのみ |
| 15 | 中断・復旧・所有権 | `cli/run.py`（`status`／`resume`／`cancel`）、`storage/_ownership.py`、`application/_run_reconciliation.py` | `tests/component/application/test_run_cancellation.py`、`test_run_reconciliation.py` | 合成のみ | 部分実装 | `resume` はヘルプ上「中断した同期公開を**再開せずに診断する**」であり、実行の再開機能ではない。実クラッシュからの復旧実証は0 |
| 16 | 予算管理 | `domain/budget.py`、`storage/demo_budget.py`、実装ノート §3・§8 | `tests/unit/contracts/test_budget.py`、`tests/component/storage/test_demo_budget.py` | 予約台帳による会計（合成flow・手動flow） | 実装済 | 予算超過の実地試験は未実施。LLM予算は実呼出0のため未検証 |
| 17 | 記録保護（実CAE・資格情報のGit混入防止） | `cli/scan_cae_data.py`、`.gitignore` | `tests/unit/test_scan_cae_data.py` | 2026-09-23実行でPASS（278 checked／278 tracked／0 diagnostics、exit 0） | 実装済 | `02_CAE` 配下への書込保護は開発契約の文書規則に依存し、製品コード側は `cli/scan_cae_data.py:152` の検知のみ |
| 18 | 実モデルE2E（E2E-02） | — | **`tests/e2e/test_authorized_real_case.py` が存在しない** | なし | 未着手 | 計画書 §7 が実行entrypointとして挙げるファイルが未作成。E2E設定は `scope="synthetic_explicit"` のみ受理（`tests/e2e/test_installed_synthetic.py:1055`）で、実モデル用scopeが無い |
| 19 | 最終BottomFrame E2E（E2E-03） | — | **`tests/e2e/test_bottomframe_final.py` が存在しない** | なし | 未着手 | 同上。加えてBottomFrameの正式入力・許可領域・評価条件が未提供（設計仕様書 §10） |
| 20 | 実ツール試験の入口 | `pyproject.toml` のマーカー定義 | `tests/native` は `native` 2件・`llm` 1件 | — | 部分実装 | 計画書 §7 が挙げる `pytest tests/native -m "febio"` は**該当試験0件**（`febio` マーカーを付けた試験がリポジトリに存在しない） |

### 2.1 この表から読み取れる「不足の型」

1. **証拠だけが無いもの**（コードは実装済）：#7 メッシュ依存性、#12 実LLM、#14 別環境配布。→ 予算と許可さえ出れば、新規実装なしで証拠を取得できる。
2. **判定基準が未定義のもの**：#8 物理適用性、#3 surface approximation、#2 native qualification。→ 何をもって合格とするかをユーザーが決めない限り、実装しても合格を主張できない。
3. **公開インターフェースが欠けているもの**：#10 Studio `CONFIRMED` の観測ヘルパー、#13 結果の自然言語報告、#18／#19 実モデルE2Eの試験ファイルと設定scope。→ 小さな新規実装が要る。
4. **機能そのものが限定的なもの**：#11 変更種別、#15 復旧（`resume` は診断のみ）、#4 対応表の能力範囲。

## 3. ロードマップ

順序は **M1 → M2 → M3 → M4 → M5**（ユーザー指示・**確定**）。各milestoneの「範囲」「exit criteria」は、確定／提案／ASKを行ごとに分けて示す。

### M1：製品範囲と受入基準の合意

| 項目 | 内容 |
|---|---|
| 目的（確定） | M2以降の作業について「何をもって合格か」を先に固定し、証拠の取り直しを防ぐ |
| 成果物（確定） | 本書と[M1 readiness記録](../reviews/2026-09-23-product-m1-readiness.md) |
| 範囲外（確定） | `src`／`tests` の変更、新機能、実ツール実行 |

**exit criteria は2段階に分ける（提案）。**

| 段階 | 条件 | 現在地 |
|---|---|---|
| M1-A：準備完了 | ①対応表（§2）が現行コードの実読取に基づく ②M2〜M5のexit criteria草案がある ③確定／提案／ASKが分離されている ④依存関係と予算提案がある ⑤docsのみのcommitで作業ツリーに差分が無い ⑥`git diff --check` と `scripts/scan_cae_data.py` がPASS | **本task完了時点で達成**（実測値は readiness記録に記載） |
| M1-B：合意完了 | §8.3 の未回答（Q3〜Q5・Q8＝実モデルの対象・許可・物理条件・精度基準・新規予算）に回答があり、本書の「提案」が「確定」へ書き換わっている | **未達成。**§8.1 の3件は2026-09-23に回答済みだが、§8.3 が未回答のため合意完了を宣言しない |

### M2：品質境界の証拠補完（必要形状だけ）

| 項目 | 内容 |
|---|---|
| 目的（確定） | MVPで `UNVERIFIED` のまま残した品質境界を、**製品版として必要な形状に限って**埋める |
| 対象形状（提案） | 直方体治具・平面押し込みのみ。球・円柱・摩擦・非線形材料は計画書 §8 backlogのまま据え置く |

対象は3系統ある。

**(a) Studio `CONFIRMED`**

- 位置づけ（確定）：`CONFIRMED` は設計仕様書 §7 の目標範囲要件として**既存の製品契約で確定済み**であり、再承認は不要（2026-09-23 PM回答）。独立観測の要件は維持する。
- 現状（確定）：`--window-id` ＋ stdin PIPEの観測経路と `PreviewReceipt` の `CONFIRMED` 検証は実装済み。観測要求を受けてPNGを返す公開ヘルパーが無い（実装ノート §7）。
- 必要な最小変更（提案）：観測応答を作る公開ヘルパー（CLIサブコマンド1つ、または文書化された手順＋小さなスクリプト）を追加し、対象XPLTハッシュ・変数・成分・座標系・単位・最終状態・要求後に撮影したPNGを返す。既存の `LAUNCHED` 契約は変更しない。
- exit criteria（提案）：実Studio 1回の起動に対して `preview_status=CONFIRMED` となり、記録に観測者・一回限りの識別子・PNGハッシュ・表示変数と最終状態が残る。PNG自体はGit外に保持する。
- **未確定（M2着手時に確定）**：観測主体が誰か、およびその観測手順（§8.2 Q2b）。

**(b) メッシュ依存性の3段階**

- 現状（確定）：`application/_mesh_refinement.py` に3段階判定が実装済み。E2E設定は `preparation_requests` を1件または3件受け付ける。3段階の実行証拠が無い。
- 必要な最小変更（提案）：**コード変更なし**。既存の `prepare-planar --parent-revision-id` で粗→中→細を作り、実FEBioで各版を解析する。
- exit criteria（提案）：同一ケースで粗・中・細の3版が `PREPARED`、3版とも実FEBioで `SUCCEEDED`、部品要素数の増加と実測最大辺長の減少が記録され、`mesh_dependence` が事前宣言した相対力差限界と力下限に対して `PASS` になる。4回目のヤング率変更版は既存メッシュを再利用する（計画書 §5）。

**(c) 物理適用性・surface approximation・native qualification**

- 現状（確定）：いずれも固定 `UNVERIFIED`。#8は判定処理が存在しない。#3は双方向のCAD↔メッシュ境界が未確立であることを理由として明示している。#2は調査応答で常に `UNVERIFIED`。
- 方針（確定、2026-09-23 PM回答）：物理的な実物照合の状態は数値品質とは別行に表示し、未検証なら `UNVERIFIED` のまま残す（設計仕様書 §6 の既存規定どおり）。`surface_approximation` は既存契約と数値証拠に従い、**一律の必須化はしない**。
- 具体化（提案）：
  - `surface_approximation`：直方体治具・平面部品に限り、**解析的な双方向境界**（生成メッシュ面とCAD平面／直方体面の最大符号付き距離の上下界）を計算して `PASS`／`UNVERIFIED` を出す。既存の `adapters/meshing/native_surface.py` の区間算術を再利用し、実Gmsh・実FEBio予算は消費しない。
  - `native_qualification`：調査経路にもFEBio側と同じ「実体・版・ハッシュの束縛」を導入して `PASS` を出せるようにするか、**恒久的に `UNVERIFIED` のまま運用する**かをユーザーが選ぶ。合意案は後者（認定を自称しない方が開発契約と整合する）。
  - `physical_applicability_validation`：実物照合（試験片・実測）が無い限り原理的に `PASS` にできない。合意案は **`UNVERIFIED` のまま保持し、代わりに「適用根拠（材料出典・ひずみ速度・小ひずみ適用の宣言）が登録されていること」を別行の必須項目として判定する**。
- exit criteria（提案）：上記の合意案どおりに実装・記録され、`PASS` にできない項目は理由付きで `UNVERIFIED` として公開される。

### M3：実モデルE2E（E2E-02 → E2E-03）

| 項目 | 内容 |
|---|---|
| 目的（確定） | 許可された実STEPで全経路を通し（E2E-02）、最後に最終BottomFrame実モデルで同じ全経路と指定品質を通す（E2E-03） |
| 前提（確定） | 対象CAD、ケース領域、許可された操作、物理条件、評価基準の確定（計画書 §7）。実 `02_CAE` への書込は最終工程の**新規実行領域のみ**に限定し、元データを保持する |

- 不足（確定）：`tests/e2e/test_authorized_real_case.py` と `tests/e2e/test_bottomframe_final.py` が存在しない。E2E設定は `scope="synthetic_explicit"` しか受理しない。
- 必要な最小変更（提案）：E2E設定に `scope="authorized_real"` を追加し、実モデル用の設定項目（対象STEP、許可書込先、ケース領域、物理条件JSON、評価基準、予算）を型付きで受理する。試験ファイルは合成E2Eの構造を流用し、**合成STEP固有の前提（体積・面数の期待値など）を実モデルへ持ち込まない**。
- exit criteria（提案、モデル1件あたり）：STEP調査 → 条件確定（根拠付き）→ 版固定 → 剛体・メッシュ生成 → 実FEBio `SUCCEEDED` → 必須品質の判定 → Studio表示 → 1項目以上の編集 → 新規解析 → 比較、までが公開CLIで通り、`docs/reviews/` に対象SHA・変更ファイル・実コマンドと終了コード・実FEBio／Studio起動回数・未検証事項・次の判断が記録される。実データ・実パスはGitに入れない。
- E2E-03の追加条件（確定）：BottomFrameの正式入力・条件が揃わない場合は**未実施のまま残す**（計画書 §7）。

### M4：実LLMによるAI-02（日本語→質問→変更→結果報告）

| 項目 | 内容 |
|---|---|
| 目的（確定） | 日本語でCAD解析条件を与え、不足は質問で解消し、条件変更を適用し、**結果を簡潔な自然言語で報告する**までを実LLMで通す。AI-02の3操作も「結果の簡潔な報告」も、合意済みロードマップとユーザーの目的に含まれる（2026-09-23 PM回答。要否の再承認は不要） |

- 現状（確定）：`tests/native/test_llm.py` が `llm` マーカーで存在し、日本語の `intent`（材料モデル・ヤング率）→ `NEEDS_INPUT`（終了コード3）→ `answer` → `edit` までを公開CLI経由で検証する。実LLM呼出の実績は0。
- 不足（確定）：承認済みLLM設定が未提供。**結果の自然言語報告は実装も設計仕様書の契約行も無い。**
- 必要な最小変更（提案）：比較・品質・実行の登録済み記録だけを入力として日本語の要約文を返す、読み取り専用の報告経路を1つ追加する。LLMには形状要約・明示条件・未解決事項・差分・集計値・関連ログのみを渡し（設計仕様書 §8）、提案に実行権限は与えない。設計仕様書 §8 の文言整備はM4着手時に行う。
- **未確定（M4着手前）**：provider／model／key_env／有限予算（§8.2 Q6後半）。これが揃うまで実LLMは呼び出さない。
- exit criteria（提案）：実LLM 1 flowで、日本語入力 → 質問（`ASK_AND_BLOCK`）→ 回答 → 型付き差分の適用 → 版確定 → 結果の日本語要約、までが公開CLIで通り、呼出回数・トークン使用量・操作IDが記録される。鍵・実データはログにも記録にも出さない。

### M5：新規環境配布・復旧・予算・記録保護・限定利用の受け入れ

| 項目 | 内容 |
|---|---|
| 目的（確定＋提案） | 開発機以外でも通常インストールから動作し、異常時に安全に止まり、予算と記録保護が働き、限定された利用者が受け入れられる状態にする |

- 現状（確定）：新規環境インストールからのE2E-01は開発機で成功。別機械・別ユーザーでの再現は0。`resume` は再開ではなく診断。予算超過・クラッシュ復旧は合成試験のみ。
- exit criteria（提案）：
  1. **配布**：開発機とは別の新規Python 3.12環境へ同一wheelを通常インストールし、8手順が再現する（wheelのSHA-256一致を記録）。
  2. **復旧**：実行中に所有プロセスを強制終了させ、`status`／`resume` が `INTERRUPTED` を正しく報告し、結果を完成として公開しないことを実ツールで確認する。
  3. **予算**：宣言した上限を超える要求が起動前に拒否され、失敗・中断でも消費枠が戻らないことを記録で確認する。
  4. **記録保護**：`scripts/scan_cae_data.py` がPASSし、実CAE・資格情報・ローカルパスがGitに無いことを候補コミットで確認する。`02_CAE` への書込がM3の許可領域に限られていることを記録で示す。
  5. **限定利用受入**：利用者・対象機・利用範囲・受入証拠の定義に従って受け入れ記録を残す。この定義は**提案のまま保持し、M5着手前に合意する**（§8.2 Q7、2026-09-23 PM回答）。

## 4. 依存関係

```text
M1(合意) ──► M2(品質境界) ──► M3(実モデル E2E-02 → E2E-03) ──► M4(実LLM) ──► M5(配布・復旧・限定利用)
   |            |                     |                            |             |
   |            |                     |                            |             +— Q7(限定利用の定義)をM5前に合意
   |            +— (b)3段階meshは Q8(予算)の回答だけで着手可         +— Q6後半(LLM設定)が無いと着手不可
   +— Q3/Q4/Q5(対象・許可・物理条件・精度)が無いと M3 は着手不可 ----+
```

未回答（§8.3）は該当milestoneだけを止める。M1の文書準備と、他milestoneの設計・調査は止めない。

- **確定**：実施順序は M1 → M2 → M3 → M4 → M5。
- **提案**：M4（実LLM）はM3の実モデルに依存しない。合成ケースでもAI-02は成立するため、M3が許可待ちで止まる場合はM4を先行させてよい。ただしユーザー指示の順序を勝手に上書きはしない。
- **確定**：M5はM2〜M4の記録を束ねる位置にあるため、先行milestoneの記録が揃うまで受入宣言はできない。
- **確定**：E2E-03（BottomFrame）はE2E-02の後。正式入力が無い場合は未実施のまま残す。

## 5. 予算提案（実ツール実行回数）

以前のnative予算は消費済みであり、**流用しない**。以下はすべて新規要求の**提案**である（承認前に実行しない）。

| milestone | 内訳 | メッシュ生成 | 実FEBio | Studio起動 | 実LLM呼出 |
|---|---|---:|---:|---:|---:|
| M1 | 文書のみ | 0 | 0 | 0 | 0 |
| M2 (a) Studio CONFIRMED | 既存manifestの再表示1回 | 0 | 0 | 1 | 0 |
| M2 (b) 3段階mesh依存 | 粗・中・細＋ヤング率変更（メッシュ再利用） | 3 | 4 | 0 | 0 |
| M2 (c) 適用性・近似・認定 | 解析的判定のみ | 0 | 0 | 0 | 0 |
| M3 E2E-02（実モデル1件） | 調査1・準備1・解析2・表示1 | 1 | 2 | 1 | 0 |
| M3 E2E-03（BottomFrame） | 同上 | 1 | 2 | 1 | 0 |
| M4 AI-02 | 実LLM 1 flow（計測＋生成、intent／answer／edit／報告） | 0 | 0 | 0 | 最大8 |
| M5 | 別環境の8手順1回＋復旧試験 | 1 | 3 | 1 | 0 |
| **合計** | | **6** | **11** | **4** | **8** |

- 失敗・中断も消費として数え、復元しない（実装ノート §3 の既存運用）。
- `--preflight` はネイティブソルバーを起動しないため消費しない（manual 8記録 §5）。
- M2(b)の上限「メッシュ生成3回・FEBio実行4回」は計画書 §5 の既定に一致する。

## 6. 最小実施候補（予算を絞る場合）

全部を一度に承認しない場合の最小経路を**提案**する。

| 優先 | 実施内容 | 追加予算 | 得られるもの | 落とすもの |
|---:|---|---|---|---|
| 1 | M2(b) 3段階mesh依存のみ | mesh 3／FEBio 4 | 品質6項目目が埋まり、MVPで唯一許容した `UNVERIFIED` が解消する。コード変更0 | Studio `CONFIRMED`、適用性 |
| 2 | M3 E2E-02（実モデル1件） | mesh 1／FEBio 2／Studio 1 | 実モデルで全経路が通る最初の証拠。製品としての説得力が最も上がる | BottomFrame、精度保証 |
| 3 | M4 AI-02（合成ケースで可） | LLM 最大8 | 最終像の「日本語で指示し、結果を日本語で受け取る」が実証される | provider／model／key_env／予算の提供が前提 |
| 4 | M2(a) Studio CONFIRMED | Studio 1＋小規模実装 | 表示境界が `LAUNCHED` から `CONFIRMED` へ上がる | — |
| 5 | M5 別環境配布・復旧 | mesh 1／FEBio 3／Studio 1 | 他人の環境で動く証拠 | — |
| 6 | M3 E2E-03（BottomFrame） | mesh 1／FEBio 2／Studio 1 | 最終受け入れ | 正式入力が無ければ未実施のまま |

**おすすめ**：まず優先1（コード変更0・実モデル許可不要・効果大）を実施し、並行して §8 のQ3〜Q5に回答をもらって優先2へ進む。

## 7. 各milestoneで触らないもの（確定）

- 保護ブランチ `native-curved-primitives`／`acceptance-gate-repairs`、およびsoft-holdの作業ツリーは削除しない。
- 旧manual `case-6294a0a9e02c`（preparation `FAILED`／`ABORTED/BLOCKED`）と受理済みautomated `case-80e5f42a5043` は、retry／reset／代替case／証拠の付け替えを行わない。
- `adapters/geometry/_gmsh_runtime.py`（依存関係認証）は正常動作している限り触らない。
- 新たなハッシュ固定・自己ハッシュ・追加の権限レイヤーは導入しない。
- V2への統合と `git push` はPMが担当する。

## 8. 質問と回答の状態（2026-09-23 PM回答を反映）

質問は8件出した。うち3件は回答済み、3件は該当milestone直前に合意する項目、2件（実際には4問）は開発部長への照会中で未回答である。**一括承認（「全部合意案どおり」）は、物理値の未指定を解消できないため選択肢から撤去した。**

### 8.1 回答済み（確定）

| # | 質問 | 回答（2026-09-23、PM経由） |
|---:|---|---|
| Q1 | `physical_applicability_validation` と `surface_approximation` を必須合格にするか | **既存仕様どおり。**物理的な実物照合の状態は数値品質とは別行に表示し、未検証なら `UNVERIFIED` のまま残す（設計仕様書 §6）。`surface_approximation` の適用範囲は既存契約と数値証拠に従い、**一律の必須化はしない**。直方体・平面に限った解析的な双方向境界の実装は、証拠が成立する範囲でのみ `PASS` を主張する |
| Q2 | Studio `CONFIRMED` を必須にするか | **既存の製品契約で確定済み**であり、再承認は不要（設計仕様書 §7「目標範囲における要件」）。独立観測の要件は維持する。**観測主体（誰が観測するか）はM2実行時に確定する** |
| Q6前半 | 「結果の自然言語報告」を製品契約に加えるか | **合意済みのロードマップおよびユーザーの目的に含まれる**ため、新たな要否承認は不要。設計仕様書側の文言整備はM4着手時に行う |

### 8.2 該当milestone直前に合意する項目（現時点では提案のまま）

| # | 項目 | いつ確定するか | 提案（おすすめ） |
|---:|---|---|---|
| Q2b | Studio `CONFIRMED` の観測主体と観測手順 | **M2着手時** | 単独所有者1名が観測し、要求後に撮影したPNGと、表示変数・成分・座標系・単位・最終状態の記録をもって `CONFIRMED` とする |
| Q6後半 | 実LLMの provider／model／key_env／有限予算 | **M4着手前**（現時点では未確定事項） | `FEBIO_CAE_NATIVE_LLM_SETTINGS` で承認済み設定を提供してもらう。モデルの自動代替はせず、鍵は引数・ファイル・ログ・エラーへ出さない（実装ノート §8） |
| Q7 | M5「限定利用」の定義（利用者・対象機・受入証拠） | **M5着手前** | 単独所有者1名・開発機1台＋新規環境1台。新規環境での通常インストールと8手順の再現、復旧・予算・記録保護の各1件をもって受け入れとする |

### 8.3 未回答（ASK_AND_BLOCK、開発部長へ照会中）

**これらが返るまで、依存する作業は実行しない。**

| # | 質問 | 影響するmilestone | 提案（回答が無い限り実行しない） |
|---:|---|---|---|
| Q3 | E2E-02の対象実STEPはどれか。書込を許可する領域はどこか。部品と治具の役割はどう割り当てるか | M3 | STEP 1件を指定してもらい、書込先は `02_CAE` **外**の新規領域に限定する。治具は新規生成の直方体剛体、部品は指定STEPの閉じたソリッド1つ |
| Q4 | E2E-02／03の物理条件：材料（ヤング率・ポアソン比・出典）、支持面、押し込み方向と量、接触対、摩擦、評価領域、許容差 | M3 | **推測しない。**型付きJSONで提供してもらう。形式は既存の `SpecUpdateRequest` と同じで、MVPと同じ「等方線形弾性・摩擦なし・XYZ完全固定・−Z単調変位」の枠に収まる値を想定する。一括承認ではこの未指定は解消できない |
| Q5 | 実モデルでの**精度**の合格基準は何か（解析解が無い対象で何をもって合格とするか） | M3、M5 | 必須5項目＋3段階メッシュ依存＋事前宣言した相対差限界で判定し、実物との一致（実験照合）は `UNVERIFIED` のまま残す |
| Q8 | 新規の実ツール実行予算（§5 の提案：メッシュ6／実FEBio 11／Studio 4／LLM 8）を承認するか。絞る場合は §6 のどの優先まで承認するか | 全体 | まず §6 の優先1（メッシュ3／実FEBio 4）だけを承認し、Q3〜Q5の回答後に優先2を追加承認する。**以前のnative予算は消費済みであり流用しない** |

## 9. 合意完了（M1-B）の宣言条件

次のすべてが揃ったときに限り、M1の**合意完了**を宣言する。

1. §8.3 のQ3〜Q5・Q8に回答があり、本書の該当行が「提案」から「確定」へ書き換わっている。
2. 新規の実ツール実行予算が明示的に承認されている（§5 全体、または §6 のいずれかの優先まで）。
3. M2の着手対象（(a)(b)(c)のどれを、どの順で）が決まっている。
4. 上記を反映した本書の改訂がcommitされ、作業ツリーに差分が無い。

§8.1 の3件は回答済みだが、§8.3 が未回答である以上、**M1は合意完了ではない**。回答が揃うまでは **M1-A（準備完了）までを到達点として報告する**。準備が終わったことを、範囲の合意が終わったことへ読み替えない。なお §8.3 の未回答は該当milestoneだけを止めるものであり、M1の文書準備そのものは止めない。
