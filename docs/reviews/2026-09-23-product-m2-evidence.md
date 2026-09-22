# M2 実数値証拠・形状調査／Studio確認保留

## 現在地と対象

**M2(b)の3段階メッシュとE-only継承は合格、M2(c)の調査記録は完了、M2(a)の独立Studio確認は未達。M2全体・製品全体の完了は宣言しない。** M3は実モデルの対象・使用許可・物理条件・評価基準・実行予算が未確定で `ASK_AND_BLOCK` を維持する。

対象runtime source=`71c388f229dbfabc9fffb48c90c4ca9c1def5ee9`、format=`0e35ba4d5b497f6d55d790e9b4b650b7b6dc9de6`、観測ブリッジ・文書候補=`cbd33e48d00681ad2051b8d3f081cd67d3f10a04`。本記録を含む候補commitは完了報告の実SHAを正とする。受理済み通常wheel（404193 bytes、SHA-256 `f978de60803f70b9a5858a60bae185e0ed467949b0e062eafd2826a5e18b2cdd`）の既存installed環境を `-I -B` で使用し、開始・終了時の107 package membersは一致した。今回のnative実行では再build・環境変更・製品コード変更・pytestは0。

変更ファイルは本記録と `docs/plans/2026-09-23-product-roadmap.md` の2件。ブリッジ変更6件・29試験の証拠は[準備記録](2026-09-23-product-m2-preview-preparation.md)に分離する。

Git外の証拠を `<M2>`=`<COORDINATION>/product-m2-20260923-native`、`<PREVIEW>`=`<COORDINATION>/product-m2-20260923-preview` と記す。実パス・生の解析結果・PNGはGitへ入れない。

## 実行範囲と会計

承認 `msg_757df3e48e7a` に基づく新しい合成ケース `case-2116821c8683` のみ。既存case、実CAEモデル、保護ブランチ、soft-hold作業ツリーは変更しない。物理条件・許容値は事前指定のままで、変更は新しい調査への参照結合、global mesh size、承認枠に合わせた行政上のattempt上限のみ。

| 操作 | 予約・消費 | 実成功 | 上限 |
|---|---:|---:|---|
| native inspection | 1 | 1 INSPECTED | 600秒、CPU 1 |
| native preparation | 3 | 3 PREPARED | 各570秒、CPU 1、節点250000・四面体100000 |
| 実FEBio | 4 | 4 SUCCEEDED | 各3600秒、CPU 1 |
| Studio（PM別枠） | 1予約を保持 | 新規起動0、CONFIRMED 0 | 起動前競合拒否後の続行判断待ち |
| native再試行・予算復元 | 0 | — | 失敗停止・再試行0 |

native担当の残枠はinspect／prep／FEBioとも0。`--preflight` 4件はsolverを起動していない。Studio枠は返却・復元していない。

## 数値証拠

| 版 | revision | 部品要素数 | 実測最大角節点辺長 [m] | run / quality |
|---|---|---:|---:|---|
| coarse | `revision-330358830b29` | 325 | 0.001053305393545568 | SUCCEEDED / PASS |
| refined | `revision-2acf3114f5a7` | 889 | 0.000696951706180756 | SUCCEEDED / PASS |
| fine | `revision-30d344675ea7` | 1966 | 0.0005151735317362838 | SUCCEEDED / PASS |
| child | `revision-b062655dbd20` | 1966 | 0.0005151735317362838 | SUCCEEDED / PASS |

全4runで既存の必須5数値項目が `PASS`。粗・中の `mesh_dependence=UNVERIFIED` は3段階が揃う前の状態として保持し、fineとchildでは6項目すべて `PASS` となった。部品要素数325→889→1966、最大辺長の単調減少を実測した。

事前のglobal sizeは0.0005／0.0003535533905932738／0.00025 m、相対力差限界0.02、力下限1e-8 N、材料正規化差限界0.01。各比較は全保存11状態のWorld-z力履歴を用いた。

| 比較 | 最大相対差 | 判定 |
|---|---:|---|
| coarse → refined | 0.0007783796064404918 | PASS |
| refined → fine | 0.0003633066629653994 | PASS |
| fine → child（弾性率正規化） | 0 | PASS |

childはヤング率のみ変更し、節点・要素・面の完全一致を確認した。公開比較は `COMPARED`／exit 0、2026-09-22T17:30:25.659572Zに完了し、比較プロセスの取得した2件のpublication leaseが解放された。最終child manifestは `1a0548b6cc51a2b381f03ae7b883d507cab4216e0eab906e22ed23d5d70e67be`。XPLT SHA-256は `a526257b04522a1e6d05233e4e51c6bd4b218a427113342697bf76888d0a4bea`、1342274 bytes。

## コマンドと失敗記録

native担当は29コマンド、exit 0が27件、exit 2が2件。全件のargv・出力・開始終了時刻は `<M2>/ledger.json` と個別receiptに保持する。下表は実argvのローカルパスだけを記号化したもの。

| stage | コマンド | exit |
|---|---|---:|
| 00-version | `<INSTALLED_PYTHON> -I -B -m febio_cae --version` | 0 |
| 01-create | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> create --case-root <CASE_ROOT> --cad <SYNTHETIC_STEP> --json` | 0 |
| 02-inspect | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> inspect case-2116821c8683 --native --wall-seconds 600 --cpu-workers 1 --json` | 0 |
| 03-provision | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> provision-planar-profiles case-2116821c8683 --json` | 0 |
| 04-spec | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> spec case-2116821c8683 --file <M2>/inputs/initial-spec.json --expected-generation 0 --json` | 2 |
| 04b-spec-corrected | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> spec case-2116821c8683 --file <M2>/inputs/initial-spec-corrected.json --expected-generation 0 --json` | 0 |
| 05-coarse-prepare | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> prepare-planar case-2116821c8683 --file <M2>/inputs/coarse.json --expected-generation 1 --json` | 0 |
| coarse-validate | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> validate case-2116821c8683 --json` | 0 |
| coarse-freeze | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> freeze case-2116821c8683 --json` | 0 |
| coarse-preflight | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> run case-2116821c8683 --revision-id revision-330358830b29 --solver <FEBIO_EXE> --json --preflight` | 0 |
| coarse-run | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> run case-2116821c8683 --revision-id revision-330358830b29 --solver <FEBIO_EXE> --json` | 0 |
| refined-prepare | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> prepare-planar case-2116821c8683 --file <M2>/inputs/refined.json --expected-generation 2 --parent-revision-id revision-330358830b29 --json` | 0 |
| refined-validate | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> validate case-2116821c8683 --json` | 0 |
| refined-freeze | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> freeze case-2116821c8683 --json` | 0 |
| refined-preflight | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> run case-2116821c8683 --revision-id revision-2acf3114f5a7 --solver <FEBIO_EXE> --json --preflight` | 0 |
| refined-run | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> run case-2116821c8683 --revision-id revision-2acf3114f5a7 --solver <FEBIO_EXE> --json` | 0 |
| fine-prepare | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> prepare-planar case-2116821c8683 --file <M2>/inputs/fine.json --expected-generation 3 --parent-revision-id revision-2acf3114f5a7 --json` | 0 |
| fine-validate | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> validate case-2116821c8683 --json` | 0 |
| fine-freeze | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> freeze case-2116821c8683 --json` | 0 |
| fine-preflight | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> run case-2116821c8683 --revision-id revision-30d344675ea7 --solver <FEBIO_EXE> --json --preflight` | 0 |
| fine-run | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> run case-2116821c8683 --revision-id revision-30d344675ea7 --solver <FEBIO_EXE> --json` | 0 |
| child-instruction | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> spec case-2116821c8683 --file <M2>/inputs/e-only-instruction.json --expected-generation 4 --json` | 0 |
| child-patch | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> patch case-2116821c8683 --file <M2>/inputs/e-only-patch.json --expected-generation 5 --json` | 0 |
| child-validate | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> validate case-2116821c8683 --json` | 0 |
| child-freeze | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> freeze case-2116821c8683 --json` | 0 |
| child-preflight | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> run case-2116821c8683 --revision-id revision-b062655dbd20 --solver <FEBIO_EXE> --preflight --json` | 0 |
| child-run | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> run case-2116821c8683 --revision-id revision-b062655dbd20 --solver <FEBIO_EXE> --json` | 0 |
| compare | `<INSTALLED_PYTHON> -I -B -m febio_cae case --state-dir <STATE_DIR> compare case-2116821c8683 --file <M2>/inputs/comparison.json --json` | 2 |
| compare-corrected | `<INSTALLED_PYTHON> -I -B -m febio_cae compare run-cb802dfa6ae0 run-890513806523 --case-id case-2116821c8683 --state-dir <STATE_DIR> --spec <M2>/inputs/comparison.json --json` | 0 |

`04-spec` は一部参照のnull geometry bindingで拒否された。PMの明示回答に従い、同じinspectionの参照だけを補い `04b-spec-corrected` が成功した。`compare` はCLI group指定を誤り、既存公開top-level構文に直した `compare-corrected` が成功した。失敗2件は削除せず、物理条件・許容差・native消費を変えていない。

PMは `python -I -B -m febio_cae case --state-dir <STATE_DIR> preview case-2116821c8683 --manifest-id <CHILD_MANIFEST> --studio <STUDIO_EXE> --json` を1回要求した。2026-09-22T17:26:09ZにStudio枠1を予約し、比較が完了する前に呼んだため、17:26:39Zにexit 8／`INVALID_INPUT`／`registered publication owner is still busy` で停止した。これはPMの操作順序の誤りであり、solverの失敗ではない。

`application/_preview.py:115` のtransaction取得が `subprocess.Popen` より前にあり、`storage/_ownership.py:154` の30秒ロック待ちで拒否された。プロセス観測も既存Studioだけで、新しいStudioは起動していない。`<PREVIEW>/launch.json`、stdout／stderr、`conflict-evidence.json` を保持し、予約を復元せず再実行もしていない。表示値・PNG・CONFIRMED receiptは未取得。次回は比較完了・所有権解放を確認した後にのみ表示要求へ進む。

## M2(c) 形状品質・対応資格

- `native_qualification`：公開inspectは設計仕様書§4および `_inspection.py:84` で `UNVERIFIED` を返す契約。Gmshの識別照合やINSPECTEDだけでは資格の証拠にならない。対象形状・要素・解析・ツール範囲に対する適合証拠と受入範囲の明示が必要であり、今回の読取調査では契約を変更しない。
- `surface_approximation`：`_preparation.py` とgeometry adapterは理由付き `UNVERIFIED`。`native_surface.py` の片方向境界偏差や最小符号付き距離区間は、CAD全表面との双方向Hausdorff境界を確立しない。平面・直方体でも有限CAD面と完全なメッシュ面集合の対応、両方向の被覆・距離境界が未証明であり、正のTet10体積や片方向tool-boundary PASSで置き換えない。
- `physical_applicability_validation`：実物との照合証拠が無いため、数値品質とは別行の `UNVERIFIED` を維持する。合成ケースのメッシュ依存性合格を実モデル適用の証拠にしない。

上記調査のコード変更・試験・native起動は0。後続証明に必要な欠落を記録したもので、新しい権限層・ハッシュ束縛・必須品質項目は追加していない。

## 証拠と候補検査

`<M2>/result.json` SHA-256=`2c183748f120430d09a27e0dd596d0f831af3e2cbfe50c94df525796346ca4e7`、`ledger.json`=`7e47e793f9153daa52386c1a18cda22fe4e45ddf99f4f81723f9fbfbd44732f0`、`studio-handoff.json`=`1d0db89a1db49ff1aa3a137d4b30f73f37c370116eae9a0ee67a055028b6e319`。PMは3ファイルとも実ハッシュ一致を確認した。出力XPLT／solver logの8ファイル、107 installed package members、29個別receiptの検証記録は `<M2>/verification.json` と `evidence/` にある。

記録追記後の `git diff --check` と `git diff --cached --check` はexit 0・指摘0。2文書をstageした `python scripts/scan_cae_data.py --root .` はexit 0、PASS、checked／tracked／index各284、diagnostics／issues各0。今回の記録追記でpytest／build／solverの再実行は0。検査記録を追記した最終indexにも同じ検査を適用する。

## 未達と次の判断

M2(a)の `CONFIRMED`、実モデルE2E、最終BottomFrame E2E、実LLM経路は未検証。MVP完了の既存判定は変更しない。

Studioについては、失敗記録と同じ予約を保持したまま、解放済みの同じ最終manifestで最初の実起動1回を行う続行案を指定判断先へ照会した（`msg_fec8e8654d6e`）。M3の根拠付き入力の取得方法も照会済み（`msg_cbda9366d956`）。指定先は接続中だがPowerShell待機画面で、判断主体の稼働・既読を確認できていない。判断先の復旧または有効な宛先の指定を待ち、表示を自動再試行しない。
