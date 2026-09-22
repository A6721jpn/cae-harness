# MVP manual 8手順 実行記録（公開CLI手動flow・PASS）

- 記録更新日（workstation local date）：2026-09-22
- 対象：許可済みsynthetic STEPによるMVP候補の**手動**8手順。実モデル、`02_CAE`、資格情報は対象外
- 記録状態：`MANUAL_8_PASS`。計画書 §2 の8手順を公開CLIの手動実行で完遂した。MVP完了判定は計画書 §2 に基づき本記録と[native-enabled automated記録](2026-09-20-mvp-planar-e2e.md)で判定する。**最終project完成は宣言しない**
- 実行者：単独所有者1名（Claude Opus 5／`claude-opus-5`、native実行、Codexフォールバックなし、サブエージェント0）
- 証拠ルート：`<COORDINATION>`=元のチェックアウト/.local/coordination。Git管理外であり実パスは記録しない
- 本flowの記録：`<COORDINATION>/mvp-20260922-manual-final-opus/`（`result.json`、`reservation-ledger.json`、`identity.json`、`receipts/*.json`、`generated-inputs/*`）

## 1. 実施根拠と分離

ユーザーSATOUによる2026-09-22のPAUSE解除と、Run `run_b14aa0feb047` の指示（manual final flowを1本、inspection1／preparation1／FEBio2／Studio1、古いFAILEDケースを混ぜずリトライしない）に基づく。追加のユーザー承認確認は不要とされた。物理条件・許容差・既存時間上限は変更していない。

本flowは既存記録から完全に分離した新しいstate／case-root／receipt上で実施した。

| 対象 | 扱い |
|---|---|
| 旧manual `case-6294a0a9e02c`（preparation `FAILED`／570 s） | 一切触れていない。retry／status／reset／takeover／結果差替なし |
| 受理済みautomated `case-80e5f42a5043` | 一切触れていない。本manual flowの成功として付け替えない |
| 本flowのcase | 新規 `case-bb9e975f3fb3`（新state-dir／新case-root） |

`pytest`のE2E再実行でmanualを代替していない。本flowでの`pytest`実行は0、`python -m build`は0、`src`／`tests`の変更は0である。

## 2. 入力identityと実行環境

実行前に全入力を読み取り専用で照合し、期待値と一致した（`<COORDINATION>/mvp-20260922-manual-final-opus/identity.json`）。

| 対象 | SHA-256 |
|---|---|
| wheel `febio_cae-0.1.0-py3-none-any.whl`（size 404193） | `f978de60803f70b9a5858a60bae185e0ed467949b0e062eafd2826a5e18b2cdd` |
| installed Python（既存fresh env、通常インストール済みwheel CLI） | `0b471133e110cfb53a061cad528ce8e517d7b9ac41a0a396c39ad795a487fc14` |
| gmsh 4.15.2 module `gmsh.py` | `a56ebe69dc57a3ea15eee191cae4f1881b06784174b2d9df306a98bdd8b06606` |
| gmsh 4.15.2 library `gmsh-4.15.dll` | `6cac3eefb477265d9fa60bbd869dbbbf7c7ca4cb308c0f8f2b43e91d43de3c1c` |
| 合成STEP `box.step`（size 15377） | `a686ada6e0a012835175bcce8b2c07da99623f410d2dc14086229fbc7c8d4f73` |
| 承認済み準備要求 `mvp-request.json`（size 66766） | `f4c9501f724a518f9eeda00dd9f2ed8cfb0a12422fc9bcd99a52b9546ee2e915` |
| FEBio 4.12.0 `febio4.exe` | `03b9db12c4b3e2ed0cf027be6b8b5d0cef2d4edc9eab26f5a2bd8193efb770c9` |
| FEBio Studio `FEBioStudio.exe` | `703ae324ae46ab03e9e5389116fa90af5effe39364ed136dd6041ab8264252ae` |

source=`71c388f229dbfabc9fffb48c90c4ca9c1def5ee9`、test-only=`56e122b7d1e2c4a0c30aac477db06d812cb62b69`、format-only=`0e35ba4d5b497f6d55d790e9b4b650b7b6dc9de6`。開発sourceの直接importでCLIを代替していない。

### 入力の再bind範囲

新caseに必要なcase／source／profile／inspection参照だけを再bindし、物理材料・荷重・拘束・接触・ROI・許容差・予算は推測変更していない。

- `initial-spec.json`：承認済み`mvp-request.json`から`preparation`ブロックを除き、本caseの`INSPECTED`応答由来の`geometry_digest`／`inspection_digest`のみを束縛。この2項目を外すと承認済み要求と完全一致することを機械検証した
- `e-only-instruction.json`：承認済み設定の指示文とそのdigestをそのまま使用
- `e-only-patch.json`：親revision ID・親spec digest・根拠参照はいずれも本caseの実CLI応答から取得。親材料との差分は`material.youngs_modulus`（1e6→2e6 Pa）とその根拠のみであることを機械検証した
- `comparison.json`：軸・`fixed_conditions`・`intended_changes`は承認済み設定のまま。manifest IDと新規`comparison_id`のみ束縛

## 3. 実行ledger（22コマンド・全exit 0）

各段階の識別子はすべて実CLI出力から取得した。実argv／cwd／開始終了時刻／exit／応答は`<COORDINATION>/mvp-20260922-manual-final-opus/receipts/<stage>.json`および`result.json`の`steps[]`を正とする。CLI argvの記録用wrapperのみ使用し、製品の挙動は変更していない。

| # | stage | status | exit | 秒 | native |
|---:|---|---|---:|---:|---|
| 0 | `installed-runtime-probe`（`--version`） | `febio-cae 0.1.0` | 0 | 0.3 | – |
| 1 | `create` | `REGISTERED` | 0 | 0.5 | – |
| 2 | `inspect --native --wall-seconds 600 --cpu-workers 1` | `INSPECTED` | 0 | 135.2 | inspection 1 |
| 3 | `provision-planar-profiles` | `PROVISIONED` | 0 | 0.5 | – |
| 4 | `spec --expected-generation 0` | `UPDATED` | 0 | 0.3 | – |
| 5 | `prepare-planar --expected-generation 1` | `PREPARED` | 0 | 265.8 | preparation 1 |
| 6 | `validate` | `VALIDATED` | 0 | 2.8 | – |
| 7 | `freeze` | `FROZEN` | 0 | 3.3 | – |
| 8 | `run --preflight`（baseline） | `PREFLIGHT_PASSED` | 0 | 5.2 | – |
| 9 | `run`（baseline） | `NEEDS_PREVIEW`／run `SUCCEEDED`／quality `PASS` | 0 | 52.2 | solver 1 |
| 10 | `status`（preview前） | `STATUS` | 0 | 30.3 | – |
| 11 | `preview --studio` | `LAUNCHED`／task `COMPLETE` | 0 | 50.7 | Studio 1 |
| 12 | `preview-status` | `LAUNCHED`／task `COMPLETE` | 0 | 35.7 | – |
| 13 | `status`（preview後） | `STATUS`／task `COMPLETE` | 0 | 67.4 | – |
| 14 | `spec`（E-only根拠、`--expected-generation 2`） | `UPDATED` | 0 | 0.3 | – |
| 15 | `patch`（`--expected-generation 3`） | `UPDATED` | 0 | 0.4 | – |
| 16 | `validate`（子） | `VALIDATED` | 0 | 2.2 | – |
| 17 | `freeze`（子） | `FROZEN` | 0 | 3.0 | – |
| 18 | `run --preflight`（candidate） | `PREFLIGHT_PASSED` | 0 | 7.1 | – |
| 19 | `run`（candidate） | `NEEDS_PREVIEW`／run `SUCCEEDED`／quality `PASS` | 0 | 55.8 | solver 2 |
| 20 | `status`（candidate） | `STATUS` | 0 | 31.2 | – |
| 21 | `compare` | `COMPARED` | 0 | 71.1 | – |

実行区間は`2026-09-22T13:06:42Z`〜`2026-09-22T13:28:28Z`、コマンド実時間合計821.3 s。

### 識別子

`case-bb9e975f3fb3`／prepared `revision-c89eb3cb6652`（spec digest `13a77b07…`、preparation `020265ae4f17…`、mesh digest `09c595b4…`）／child `revision-18be86857fc4`（spec digest `6a5c9738…`、親は prepared revision）／baseline `run-a55fc25bbaa8`・`attempt-9a5a004872d4`・manifest `0692bc07…`／candidate `run-ae44b2e4ea37`・`attempt-151b14cbfaa0`・manifest `41b62e44…`／preview `preview-b88a9cb4c2984b21ae76533d2cc18685`／comparison `synthetic-comparison-a89cdc69a53b4ff38e258c5c2fe68a52`（record digest `064522f2…`）。

## 4. 計画書 §2 の8手順に対する判定

| # | 手順 | 結果 |
|---|---|---|
| 1 | `case create` → `inspect --native` | `REGISTERED`→`INSPECTED`。body 1件・閉じたソリッド・単位`mm`・体積`6.000000000000001e-09 m³`・面6を取得。`native_qualification`は`UNVERIFIED`のまま |
| 2 | `provision-planar-profiles` | `PROVISIONED`。`--bundle-path`未使用、製品組み込み既定の対応表のみ |
| 3 | `spec` | `UPDATED`、generation 1、`unresolved_fields`は0。形状・選択集合・メッシュの準備前に`READY`と判定していない |
| 4 | `prepare-planar`→`validate`→`freeze` | `PREPARED`（tet10、mesh生成1回、上限570 sに対し265.8 s）→`VALIDATED`（unresolved 0）→`FROZEN`。**freezeのrevision IDは`PREPARED`と同一の`revision-c89eb3cb6652`** |
| 5 | `run` | `PREFLIGHT_PASSED`の後、実FEBio 4.12.0で`run_status=SUCCEEDED`。結果manifestを公開 |
| 6 | 品質 | `quality_status=PASS`。必須5項目 `execution_result_completeness`／`contact_quality`／`motion_support_contact_fidelity`／`quasistatic_equilibrium`／`solver_residual` はすべて`PASS`、`mesh_dependence`のみ`UNVERIFIED`（MVP許容） |
| 7 | `preview` | 実Studio起動で`LAUNCHED`、`task_status=COMPLETE`。対象XPLT digest `1ff01e55…`はbaseline manifestの`output/results.xplt`と一致 |
| 8 | ヤング率変更→`run`→`compare` | E-only根拠登録→`patch`→`validate`→`freeze`で子版を作り、同一メッシュ再利用で再解析。`compare`は`COMPARED` |

**手順4は旧manual incidentが`FAILED`した地点である。時間上限570 sを変更しないまま265.8 sで`PREPARED`に到達した。**

### 手順7の境界

起動前の既存FEBio Studioプロセスは0件であり、起動後に観測されたのは所有PID 31188のみ、実行ファイルパスとdigestは登録値と一致した。他のStudioセッションを閉じたり操作したりしていない。`LAUNCHED`は起動とファイル引き渡しの証拠であり、表示内容を独立観測した`CONFIRMED`ではない。画面キャプチャは取得しておらず、receiptの`version`は`UNVERIFIED`（ネイティブファイルメタデータにversionなし）である。

### 手順8の比較値

意図した変更は`material.youngs_modulus` `1e6→2e6 Pa`のみ。子版はbaselineのpreview記録を継承せず`task_status=NEEDS_PREVIEW`である。

- 同一メッシュ再利用：node 717／element 331／face 250 が baseline・candidate で完全一致（本flow自身のregistryを読み取り専用で照合）
- force軸 `tool_compression.force_z`（6点）：relative differences `[1,1,1,1,1,1]`
- displacement軸 `tool_compression.part_peak_abs_displacement_z`（6点）：difference と relative differences `[0,0,0,0,0,0]`

比較は記述的であり、精度保証・材料妥当性・優劣ランキングを示さない。これは受理済みautomated flowの比較値と同値だが、両者は別々のflowとして保持し、証拠を付け替えない。

## 5. native会計

予約台帳は最初のnative要求より前に追記し、各native要求は発行時点で消費として記録した（失敗・中断でも消費が戻らない運用）。

| 区分 | 予約 | 消費 |
|---|---:|---:|
| inspection | 1 | 1 |
| preparation | 1 | 1 |
| solver（実FEBio） | 2 | 2 |
| Studio | 1 | 1 |

上限厳守、超過なし。重複実行・retry・reset・takeoverはいずれも0。`--preflight`はネイティブソルバープロセスを起動しないため予約を消費しない。ソルバー出力は書き換えていない。

## 6. 未検証事項

- 全体メッシュ依存性`mesh_dependence`：`UNVERIFIED`（MVPで許容）
- `physical_applicability_validation`：`UNVERIFIED`
- `surface_approximation`：`UNVERIFIED`
- Studioの表示内容：`LAUNCHED`のみ。独立観測による`CONFIRMED`は未取得、画面キャプチャなし
- 実LLM経路（AI-02）、実モデルE2E（E2E-02）、最終BottomFrame実モデルE2E（E2E-03）

これらはMVPの追加条件ではない。MVP達成は最終project完成を意味しない。

## 7. 次に必要な判断

- `git push`およびV2への追加統合はPMが担当する
- 独立したStudio `CONFIRMED`観測を行うか（MVP条件ではない）
- メッシュ依存性調査に予算を割くか（MVP条件ではない）
