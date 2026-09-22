# MVP planar E2E 実行記録（native-enabled automated PASS・旧manual incidentの記録）

- 記録更新日（workstation local date）：2026-09-22
- 対象：許可済みsynthetic STEPによるMVP候補。実モデル、`02_CAE`、資格情報は対象外
- 記録状態：`NATIVE_AUTOMATED_PASS`。native-enabled automated synthetic flowはPASS。本書は**automated flowと旧manual incident**の正本である。MVPで残件だったmanual 8手順は2026-09-22に別の新flowで完遂し、その正本は[manual 8実行記録](2026-09-22-mvp-manual-final.md)とする。本書に記載の旧manual `case-6294a0a9e02c`は`FAILED`／`ABORTED/BLOCKED`のまま保持し、新flowの成功へ読み替えない。最終project完成は宣言しない
- 証拠ルート：`<COORDINATION>`=元のチェックアウト/.local/coordination、`<ROOT_PYTEST>`=元のチェックアウト/.local/pytest-basetemp、`<WORKTREE_PROOFS>`=Orca開発ワークツリー/.local。`<TEST_PYTHON>`=Orca開発ワークツリー/.venv/Scripts/python.exe（pytest runner、installed CLI用Pythonとは別）。いずれもGit管理外であり、実パスは記録しない
## 現行受け入れ状況（current candidate 0e35ba4／f978de・native-enabled automated PASS）

> 2026-09-22追記：以下の「manual 8は未完了」という記述は、本書が扱う旧manual `case-6294a0a9e02c`についての事実であり、現在も変更していない。MVPの必須記録であったmanual 8手順そのものは、同じsource／wheel／環境・同一の物理条件と時間上限のもとで、新規case `case-bb9e975f3fb3`による別flowとして2026-09-22に完遂した（[manual 8実行記録](2026-09-22-mvp-manual-final.md)）。両flowの証拠は付け替えない。

現行candidateはsource=`71c388f229dbfabc9fffb48c90c4ca9c1def5ee9`、test-only=`56e122b7d1e2c4a0c30aac477db06d812cb62b69`、format-only=`0e35ba4d5b497f6d55d790e9b4b650b7b6dc9de6`、wheel=`<COORDINATION>/mvp-20260921-final-build-0e35ba4/febio_cae-0.1.0-py3-none-any.whl`、SHA-256=`f978de60803f70b9a5858a60bae185e0ed467949b0e062eafd2826a5e18b2cdd`、size=`404193`である。最終標準full-suite（56e122b）は`1798 passed / 0 failed / 3493.86 s`、exit 0。native-enabled final flowはgmsh 4.15.2でpytest 1 passed／942.46 s／exit 0、22 stage全exit 0、inspection1／preparation1／solver2／Studio1、coarse／candidate両run `SUCCEEDED`、必須5 numerical statuses `PASS`、baseline `COMPLETE`／Studio `LAUNCHED`／candidate comparison、same_mesh_reuse=trueを取得した。比較はYoung's modulus `1e6→2e6 Pa`、force axis 6点のrelative differences `[1,1,1,1,1,1]`、displacement axis 6点のdifference／relative differences `[0,0,0,0,0,0]`である。raw report labelとmesh `UNVERIFIED`は原因を推定せず別境界として記録する。

2026-09-21の旧manual `case-6294a0a9e02c`はpreparation `FAILED`／`ABORTED/BLOCKED`で、solver／Studio／compareを実行していない（履歴）。full-suiteの履歴は`INTERRUPTED_TIMEOUT`／非PASS、修正版71c388fのpostfix標準1798-test full-suiteはexit 1（1797 passed／1 failed、3521.67 s、sole failure=`tests/component/geometry/test_gmsh_runtime_identity.py::test_nested_optional_import_source_flow`、`RuntimeBindingError: finalize._dirty changed`）で完了した。test-only commit `56e122b`の最終標準1798-test full-suiteはexit 0（1798 passed、3493.86 s）で完了した。fresh auto runner setup `0e35ba4`はpytest欠落でcollection前に停止し、missing-Gmsh corrected flowはreport before-launchの`UNSUPPORTED_ENVIRONMENT`（observed childなし）として履歴保持した。続くnative-enabled final flowは上記current candidateの受理対象である。

### 最終候補の検証（source／format／wheel／native）

最終候補の検証として、format-only AST同一、ruff format／check、mypy、CAE scan、wheel smoke、default PyPA buildはPASSである。focused 18 passedは251版E2E実施時の履歴であり、現行candidateの最終full-suite `1798 passed`と混同しない。test-only `56e122b`のGmsh identity focusedは84 tests／8.89 s、format-only `0e35ba4d`後もAST同一・blank lineのみである。default build receiptは`<COORDINATION>/mvp-20260921-final-build-default-0e35ba4/build-receipt.json`、format equivalence receiptは`<COORDINATION>/mvp-20260921-full-suite-final-56e122b/format-equivalence-final.json`、native final receiptは`<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/launch-record.json`を正とする。

### 現行native finalの22-command ledger

`<ROOT_PYTEST>/mvp-20260921-final-auto-native-0e35ba4/test_installed_synthetic_cli_f0/installed-synthetic-attempt-report.json` の `commands[].argv` が現行22-commandの実argv ledgerであり、`<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/final-accounting.json` の `commands` 配列が対応するstage／status／exit ledger（`command_count=22`）である。全22件がexit 0。実行wrapperのpytest argvは同ディレクトリの`launch-record.json` `command_argv`に保存する。

| # | stage | status | exit |
|---:|---|---|---:|
| 0 | `installed-runtime-probe` | `FINISHED` | 0 |
| 1 | `create` | `REGISTERED` | 0 |
| 2 | `inspect-native` | `INSPECTED` | 0 |
| 3 | `provision-planar-profiles` | `PROVISIONED` | 0 |
| 4 | `initial-spec` | `UPDATED` | 0 |
| 5 | `coarse-prepare-planar` | `PREPARED` | 0 |
| 6 | `coarse-validate-prepared` | `VALIDATED` | 0 |
| 7 | `coarse-freeze-prepared` | `FROZEN` | 0 |
| 8 | `coarse-preflight` | `PREFLIGHT_PASSED` | 0 |
| 9 | `coarse-real-run` | `NEEDS_PREVIEW`／quality `PASS` | 0 |
| 10 | `baseline-status-before-preview` | `STATUS` | 0 |
| 11 | `baseline-preview` | `FINISHED` | 0 |
| 12 | `baseline-preview-status` | `FINISHED` | 0 |
| 13 | `baseline-status-after-preview` | `STATUS` | 0 |
| 14 | `register-e-only-evidence` | `UPDATED` | 0 |
| 15 | `apply-e-only-patch` | `UPDATED` | 0 |
| 16 | `validate-child` | `VALIDATED` | 0 |
| 17 | `freeze-child` | `FROZEN` | 0 |
| 18 | `candidate-preflight` | `PREFLIGHT_PASSED` | 0 |
| 19 | `candidate-real-run` | `NEEDS_PREVIEW`／quality `PASS` | 0 |
| 20 | `candidate-status` | `STATUS` | 0 |
| 21 | `public-compare` | `COMPARED` | 0 |

比較のauthoritative recordは同じ`final-accounting.json`の`comparison`であり、IDは`synthetic-comparison-aeda204cc5984c368e20ecd5ec689d22`、same mesh reuseは`true`、force-z relative differencesは`[1,1,1,1,1,1]`、displacement-z differences／relative differencesは各`[0,0,0,0,0,0]`である。

## 履歴：初期観測（現行251/b28とは別）

候補`2bb49de28c4804670dafe7593cb8c2b44ed78733`のclean-installed wheelから、公開CLIの`create=REGISTERED`、`inspect --native=INSPECTED`（いずれもexit 0）を確認した。native inspection childは1回で、Gmsh 4.15.2、宣言単位`mm`、closed body 1個、6 faces、体積約`6.000000000000001e-9 m3`、`native_qualification=UNVERIFIED`である。cached defining-module bindingの独立レビューは`CHANGES_REQUIRED`だった。

`106de1b729cde799e7148f7ecf23e456bb53764f`はfocused 84 passed、cached mutation RED→GREEN、scoped ruff/mypy 0、clean wheelからの同じ`INSPECTED`を報告した。wheel SHA-256は`38066f46055bece03ef6871eb43a2e4791529b4efef239288205d317095aa7c0`。exact-commit source reviewは`ACCEPT`だが、source受理をMVPまたは最終候補の証明へ読み替えない。後続のquality criterion identity/source-local回帰は実E2E実施版source `251dd12d14b79f7526773ae55bd08716f8e6f9c2`で受理済みで、focused 18 passedとなった。

## 自動E2E候補の実測台帳（履歴・未合格）

raw report `installed-synthetic-attempt-report.json`の全stage終了コードは次のとおりである。これは品質ID修正前のattemptであり、後段の同一保存baseline再照会とは別の時点として保持する。

| stage | exit | 返却状態 |
|---|---:|---|
| `installed-runtime-probe` | 0 | `FINISHED` |
| `create` | 0 | `REGISTERED` |
| `inspect-native` | 0 | `INSPECTED` |
| `provision-planar-profiles` | 0 | `PROVISIONED` |
| `initial-spec` | 0 | `UPDATED` |
| `coarse-prepare-planar` | 0 | `PREPARED` |
| `coarse-validate-prepared` | 0 | `VALIDATED` |
| `coarse-freeze-prepared` | 0 | `FROZEN` |
| `coarse-preflight` | 0 | `PREFLIGHT_PASSED` |
| `coarse-real-run` | 6 | `NEEDS_REVIEW`（`run_status=SUCCEEDED`、品質集計は修正前の`UNVERIFIED`） |
| `baseline-status-before-preview` | 0 | `STATUS`（修正前は`NEEDS_QUALITY`、`preview_status=null`） |

このflowのnative dispatchはinspection 1、preparation 1、solver 1、Studio 0である。inspection childはPID 38812・exit 0、preparation childはPID 16632・exit 0。solverの保存記録は`state=SUCCEEDED`だが、raw observed recordのPID／exit欄は欠測であり、保存attemptのCLI exitは6、`run_status=SUCCEEDED`、`persisted_attempt_state=SUCCEEDED`として扱う。主要IDはcase=`case-af093889fe62`、revision=`revision-b3fef16ced8b`、run=`run-2b2e8e27f9e7`、attempt=`attempt-041a36d238bd`、manifest=`dd4c02735666e9e64ec70cdab0fa829c5cfd3d083b58b96ce90c8f3d0ed98843`で、preview IDは存在しない。

`251dd12d14b79f7526773ae55bd08716f8e6f9c2`で保存baselineを読み取り専用に再照会した結果は`run_status=SUCCEEDED`、必須5行`PASS`、`mesh_dependence=UNVERIFIED`、`quality_status=PASS`、`task_status=NEEDS_PREVIEW`、`preview_status=null`である（`native_recompute=false`）。これはcriterion identityの修正後評価であって、再メッシュ・追加solver・追加native inspection・final automated/manual 8-step数値flowを行った証拠ではない。旧raw flowのStudio起動は0である。

## 251版の自動E2E（履歴）

| 項目 | authoritative record |
|---|---|
| flow / 判定 | inspection 1／preparation 1／solver 2／Studio 1、22 stageの`commands-ledger.jsonl`は全exit 0、Studio `LAUNCHED`／対象XPLT読込、baseline `COMPLETE`、candidate比較はforce 6 pointsでrelative Δ=1.0（2x）、displacement Δ=0。raw overall statusは`NUMERICAL_GATE_PASSED_NOT_OVERALL`。recorded ledger=`<COORDINATION>/mvp-20260921-final-acceptance-accounting-v251dd12.json`、wheel SHA-256=`b28d4da403bc758cdae01c928cbe242f8738dabd063dd265c435a0033320e58d`、size=`404139`。この251版の自動E2Eは別caseのmanual 8を代替しない |
| 実行記録 | `<COORDINATION>/mvp-20260921-final-automated-v251dd12/pytest-console-rerun.log`（1 passed、1166.54 s、exit 0） |
| raw report | `<ROOT_PYTEST>/mvp-20260921-final-automated-v251dd12-rerun/test_installed_synthetic_cli_f0/installed-synthetic-attempt-report.json`（baseline/candidateとも`run_status=SUCCEEDED`、quality `PASS`） |
| baseline | run=`run-69ac21dc55fc`、manifest=`cc2b97a2c07fda9b4d24d32e9edb747e677c1baddef543ee535b84b97f2b6096` |
| candidate | run=`run-f9f7389bc9a8`、manifest=`7c238a9a74c9db33965682bf795b15db137bc5d0b78278c88f119210e1515b76` |
### 251版の自動E2E（履歴）の実コマンド（22 stage）

sourceは`<COORDINATION>/mvp-20260921-final-automated-v251dd12/commands-ledger.jsonl`を正とする。共通記号は`<T5_PYTHON>`=`<COORDINATION>/mvp-20260921-final-automated-v251dd12/env/Scripts/python.exe`、`<FINAL_E2E>`=`<ROOT_PYTEST>/mvp-20260921-final-automated-v251dd12-rerun/test_installed_synthetic_cli_f0`、`<FINAL_STATE>`=`<FINAL_E2E>/state`、`<FINAL_CASE_ROOT>`=`<FINAL_E2E>/case-root`、`<FINAL_INPUTS>`=`<FINAL_E2E>/generated-inputs`、`<SYNTHETIC_STEP>`=`元のチェックアウト/.local/v/native-inspection-05/producer/box.step`、`<FEBIO4_EXE>`／`<STUDIO_EXE>`は記録済み実体のsymbolic pathである。全stageのcwdは`<FINAL_E2E>/installed-cwd`。stage 0はargv[2]=`-c`、本文はargv[3]であり、本文自体は巨大なため省略するがraw ledgerのargv[3]を正とする。

| # | stage | argv | exit |
|---:|---|---|---:|
| 0 | `installed-runtime-probe` | `<T5_PYTHON> -I -c <commands-ledger.jsonl argv[3]>` | 0 |
| 1 | `create` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> create --case-root <FINAL_CASE_ROOT> --cad <SYNTHETIC_STEP> --json` | 0 |
| 2 | `inspect-native` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> inspect case-99cbf67bf098 --native --wall-seconds 600 --cpu-workers 1 --json` | 0 |
| 3 | `provision-planar-profiles` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> provision-planar-profiles case-99cbf67bf098 --json` | 0 |
| 4 | `initial-spec` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> spec case-99cbf67bf098 --file <FINAL_INPUTS>/initial-spec.json --expected-generation 0 --json` | 0 |
| 5 | `coarse-prepare-planar` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> prepare-planar case-99cbf67bf098 --file <COORDINATION>/mvp-20260920-t5-inputs/mvp-request.json --expected-generation 1 --json` | 0 |
| 6 | `coarse-validate-prepared` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> validate case-99cbf67bf098 --json` | 0 |
| 7 | `coarse-freeze-prepared` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> freeze case-99cbf67bf098 --json` | 0 |
| 8 | `coarse-preflight` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> run case-99cbf67bf098 --revision-id revision-f6c3a0791142 --solver <FEBIO4_EXE> --json --preflight` | 0 |
| 9 | `coarse-real-run` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> run case-99cbf67bf098 --revision-id revision-f6c3a0791142 --solver <FEBIO4_EXE> --json` | 0 |
| 10 | `baseline-status-before-preview` | `<T5_PYTHON> -I -m febio_cae status run-69ac21dc55fc --case-id case-99cbf67bf098 --state-dir <FINAL_STATE> --json` | 0 |
| 11 | `baseline-preview` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> preview case-99cbf67bf098 --manifest-id cc2b97a2c07fda9b4d24d32e9edb747e677c1baddef543ee535b84b97f2b6096 --studio <STUDIO_EXE> --json` | 0 |
| 12 | `baseline-preview-status` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> preview-status case-99cbf67bf098 --preview-id preview-07f4a27082cf449481ff98fc62693b58 --json` | 0 |
| 13 | `baseline-status-after-preview` | `<T5_PYTHON> -I -m febio_cae status run-69ac21dc55fc --case-id case-99cbf67bf098 --state-dir <FINAL_STATE> --json` | 0 |
| 14 | `register-e-only-evidence` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> spec case-99cbf67bf098 --file <FINAL_INPUTS>/e-only-instruction.json --expected-generation 2 --json` | 0 |
| 15 | `apply-e-only-patch` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> patch case-99cbf67bf098 --file <FINAL_INPUTS>/e-only-patch.json --expected-generation 3 --json` | 0 |
| 16 | `validate-child` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> validate case-99cbf67bf098 --json` | 0 |
| 17 | `freeze-child` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> freeze case-99cbf67bf098 --json` | 0 |
| 18 | `candidate-preflight` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> run case-99cbf67bf098 --revision-id revision-367cdb0e9774 --solver <FEBIO4_EXE> --preflight --json` | 0 |
| 19 | `candidate-real-run` | `<T5_PYTHON> -I -m febio_cae case --state-dir <FINAL_STATE> run case-99cbf67bf098 --revision-id revision-367cdb0e9774 --solver <FEBIO4_EXE> --json` | 0 |
| 20 | `candidate-status` | `<T5_PYTHON> -I -m febio_cae status run-f9f7389bc9a8 --case-id case-99cbf67bf098 --state-dir <FINAL_STATE> --json` | 0 |
| 21 | `public-compare` | `<T5_PYTHON> -I -m febio_cae compare run-69ac21dc55fc run-f9f7389bc9a8 --case-id case-99cbf67bf098 --state-dir <FINAL_STATE> --spec <FINAL_INPUTS>/comparison.json --json` | 0 |

## 受け入れ操作の累積会計

| 範囲／provenance | inspection request | inspection observed child | preparation | solver | Studio | 注記 |
|---|---:|---|---:|---:|---:|---|
| old `case-af093889fe62`（raw＋partial continuation） | 1 | PID38812／exit 0 | 1 | 2 | 1 | partial continuationを含む旧flowの合計。251版の自動E2E（履歴）／manual 8とは分離 |
| controlled `case-99cbf67bf098` | 1 | PID32736／exit 0 | 1 | 2 | 1 | controlled 251版の自動E2E（履歴）raw report |
| recovery A `case-af44450b3ec1` | 1 | PID11908／exit 0 | 1 | 2 | 1 | reportの`dispatch_counts`実値。raw report=`<ROOT_PYTEST>/mvp-20260921-final-automated-A/test_installed_synthetic_cli_f0/installed-synthetic-attempt-report.json`、`1 passed / 1290.13 s` |
| manual `case-6294a0a9e02c` | 1 | PID28144／exit 0 | owner `FAILED`（one committed row；成功prep数には算入しない） | 0 | 0 | duplicate CLI attemptはcanceled、native／reservation impactは`UNKNOWN` |
| native final `case-80e5f42a5043` | 1 | PID23196／exit 0（inspect-native observed child） | 1 | 2 | 1 | final-accounting=`<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/final-accounting.json`、pytest 1 passed／942.46 s、22 stage全exit 0。wrapper PID3844／Studio PID13152はこのobserved-child列に算入しない |
| current environment failure `case-bf267f555b8f` | 1 | report before-launch（observed childなし） | 0 | 0 | 0 | missing Gmsh `UNSUPPORTED_ENVIRONMENT`、corrected runner flowはretryなし |
| named-flow known total | **6** | **observed child 5**（missing-Gmshはchildなし） | successful 4 ＋ owner failed 1 | **8** | **4** | native inspection成功の観測5件＋環境失敗要求1件。wrapper／Studio PIDはchild数へ混ぜず、unknownを0へ補完しない |
| prior standalone inspection requests（別provenance） | 5 | observed success children 2 | — | — | — | first 3 childrenは`UNKNOWN`。named flowへ加算しない |
| known request / observed-child total（cross-flow） | **11** | **7 observed success children** | — | — | — | named 6＋standalone 5。初期3要求のchild有無は`UNKNOWN`で、native総child数の確定値ではない |

上表は既存raw report／read-only reconciliationだけから作ったknown-count tableである。duplicate CLI／reservationの実行影響は`UNKNOWN`であり、完全ledgerや新しい完了主張は作らない。旧cutoffの集計値はcurrent totalとして使わない。
## 予算境界と修復後の一回限り検証

authoritative decisionは`<COORDINATION>/mvp-20260921-case-budget-decision.json`（decision=`preparation_calls=1`／`solver_calls=2`はE2E case／flow単位であり、開発全体のlifetime capではない。`new_final_artifact_verification`はfailed numerical solveのretryではない）である。runner setup `0e35ba4`はpytest欠落でcollection前停止、real flowではなくbudget消費なし。missing-Gmsh corrected runner flow `0e35ba4-runnerfix`は同じinstalled wheel/settingsでinspect-nativeを1回実施したが、installed Gmsh unavailable／geometryの`UNSUPPORTED_ENVIRONMENT`（exit 4、native qualification `UNVERIFIED`）で停止し、preparation／solver／Studioは0、retryなし。parent/coordinator technical correction under existing verification authority（not new user budget approval）として続けたnative-enabled final flow `0e35ba4`は別native環境（gmsh 4.15.2）で、inspection 1／preparation 1／solver 2／Studio 1を消費し、22 stage全exit 0、pytest 1 passed／942.46 s、両run `SUCCEEDED`・必須5 numerical statuses `PASS`、baseline `COMPLETE`／Studio `LAUNCHED`、candidate comparisonまでを取得した。raw reportのoverall label `NUMERICAL_GATE_PASSED_NOT_OVERALL`は原因を推定せず記録し、mesh `UNVERIFIED`とは別に自動gateをPASSとして扱う。

manual `case-6294a0a9e02c`の今回の`FAILED`／`ABORTED/BLOCKED`は別境界としてretry／reset／代替caseを一切行わず、旧251/b28のE2E、missing-Gmsh／runner setup失敗、native-enabled final flow、新wheel smokeは別recordとして保持する。native-enabled automated 8-stepはPASS済みである。当時MVPで残る必須記録であったmanual 8は、2026-09-22に新規case `case-bb9e975f3fb3`の別flowで完遂した（[manual 8実行記録](2026-09-22-mvp-manual-final.md)）。本書のmanual記述は2026-09-21の旧caseについての履歴である。candidate preview／Studio `CONFIRMED`はこのMVP manual gateとは別の表示境界として記録し、MVP全体／project doneは主張しない。

## manual case duplicate preparation incident（read-only reconciliation済み・manual flow ABORTED/BLOCKED）

reconciliation artifactは`<COORDINATION>/mvp-20260921-final-manual-incident-reconciliation.json`。このincidentの二つのCLI試行は、receipt-backed proven admission／generationとは別に記録する。distinct recovery A（case=`case-af44450b3ec1`、report=`<ROOT_PYTEST>/mvp-20260921-final-automated-A/test_installed_synthetic_cli_f0/installed-synthetic-attempt-report.json`）はreportの`dispatch_counts`実値がinspection 1／preparation 1／solver 2／Studio 1、`1 passed / 1290.13 s`であり、acknowledged final automated PASSの置換ではなく、authority inferenceもしない。final automated case=`case-99cbf67bf098`は変更しない。manual caseは`UNVERIFIED/BLOCKED`のまま保持し、project doneとは主張しない。

| 試行 | 記録 | 判定 |
|---|---|---|
| recovery duplicate `bg_4` | case=`case-6294a0a9e02c`、duplicate PIDs 7152／3256、約2026-09-21 01:29 JSTにcancel（正確時刻なし） | native child／reservation impactは`UNKNOWN`。このduplicate PIDsのlock eventなしは0を意味しない |
| owner／controlled preparation | owner PID42952のlock acquired=`2026-09-20T16:29:10.696043+00:00`、released=`2026-09-20T16:38:41.422055+00:00`、held 570.726 s。preparation=`b5fd255f1fb44b648069d1b6e026e7d8`は`FAILED`、ended=`2026-09-20T16:38:41.417901+00:00`、`StorageIntegrityError`（owned-process wall deadline exceeded、570 s）。stdout／stderrは空 | owner lock／controlled failureは記録できるが、duplicate impactは依然`UNKNOWN` |
| reservation identity (read-only) | `<COORDINATION>/mvp-20260921-reservation-identity-readonly.json` confirms one committed gmsh row for `b5fd...`, sealed `input.json` 72,358 bytes, and launcher/runtime binding matching the primary auto environment (`python` SHA-256=`0b471133e110cfb53a061cad528ce8e517d7b9ac41a0a396c39ad795a487fc14`). This proves environment binding only; caller/session before reservation and owner identity remain `UNCERTAIN` | absence of duplicate output does not prove 0. Source semantics (`src/febio_cae/storage/demo_budget.py` reserve-before-child/non-refund; preparation failure retained) corroborate ledger only. Fresh begin yields a UUID and independently admitted operation normally another row; no retry/reset/new case |
| unrelated continuation | full-suite historical attempt is `INTERRUPTED_TIMEOUT`／非PASS。71c388f postfix標準full-suiteはexit 1非PASS、56e122b最終標準full-suiteはPASS。runner setup／missing-Gmsh corrected flowは履歴として停止、native-enabled final flowは22 stage全exit 0・overall `NUMERICAL_GATE_PASSED_NOT_OVERALL`まで取得済み。既存auto Studio PID40100のinitial captureとnative final Studio PID13152のinitial captureはいずれもmanual 8に数えない | 新規launchやmanual操作を追加しない |
### manual flowの実コマンド5本（receipt-backed）

`<COORDINATION>/mvp-20260921-final-manual-v251dd12/commands/` の5 stdout receiptを正とする。反復duplicateの旧argvは `<COORDINATION>/mvp-20260921-duplicate-preparation-incident.raw.txt` を参照し、下表へ重複計上しない。

| # | 実argv（symbolic path） | wrapper exit |
|---:|---|---:|
| 1 | `<INSTALLED_FINAL_PYTHON> -I -m febio_cae case --state-dir <MANUAL_STATE> create --case-root <MANUAL_CASE_ROOT> --cad <SYNTHETIC_STEP> --json` (`01-create.stdout.json`) | 0 |
| 2 | `<INSTALLED_FINAL_PYTHON> -I -m febio_cae case --state-dir <MANUAL_STATE> inspect case-6294a0a9e02c --native --wall-seconds 600 --cpu-workers 1 --json` (`02-inspect.stdout.json`) | 0 |
| 3 | `<INSTALLED_FINAL_PYTHON> -I -m febio_cae case --state-dir <MANUAL_STATE> provision-planar-profiles case-6294a0a9e02c --json` (`03-provision.stdout.json`) | 0 |
| 4 | `<INSTALLED_FINAL_PYTHON> -I -m febio_cae case --state-dir <MANUAL_STATE> spec case-6294a0a9e02c --file <MANUAL_INPUTS>/initial-spec.json --expected-generation 0 --json` (`04-spec.stdout.json`) | 0 |
| 5 | `<INSTALLED_FINAL_PYTHON> -I -m febio_cae case --state-dir <MANUAL_STATE> prepare-planar case-6294a0a9e02c --file <COORDINATION>/mvp-20260920-t5-inputs/mvp-request.json --expected-generation 1 --json` (`05-prepare.stdout.json`) | `UNKNOWN`（wrapper exit receipt欠測。stdout JSONは`INVALID_INPUT`／`StorageIntegrityError`、child stdout/stderrも空で、exitは推定しない） |

### 履歴：旧raw flowの実コマンド

元のautomated attemptは、次の公開CLI argvを実行し、`installed-synthetic-attempt-report.json`へ終了コードを保存した。`<INSTALLED_PYTHON>`、`<STATE_DIR>`等は同reportのargvに展開済みの実パスを表す。

```text
<INSTALLED_PYTHON> -I -m febio_cae case --state-dir <STATE_DIR> create --case-root <CASE_ROOT> --cad <SYNTHETIC_STEP> --json
  -> exit 0
<INSTALLED_PYTHON> -I -m febio_cae case --state-dir <STATE_DIR> inspect <CASE_ID> --native --wall-seconds 600 --cpu-workers 1 --json
  -> exit 0
<INSTALLED_PYTHON> -I -m febio_cae case --state-dir <STATE_DIR> provision-planar-profiles <CASE_ID> --json
  -> exit 0
<INSTALLED_PYTHON> -I -m febio_cae case --state-dir <STATE_DIR> spec <CASE_ID> --file <INITIAL_SPEC_JSON> --expected-generation 0 --json
  -> exit 0
<INSTALLED_PYTHON> -I -m febio_cae case --state-dir <STATE_DIR> prepare-planar <CASE_ID> --file <PREPARATION_REQUEST_JSON> --expected-generation 1 --json
  -> exit 0
<INSTALLED_PYTHON> -I -m febio_cae case --state-dir <STATE_DIR> validate <CASE_ID> --json
  -> exit 0
<INSTALLED_PYTHON> -I -m febio_cae case --state-dir <STATE_DIR> run <CASE_ID> --revision-id <REVISION_ID> --solver <FEBIO4_EXE> --json
  -> exit 6 (元のautomated attemptはFAILED。native FEBio process stateはSUCCEEDED)
<INSTALLED_PYTHON> -I -m febio_cae status <RUN_ID> --case-id <CASE_ID> --state-dir <STATE_DIR> --json
  -> exit 0
```

## 現行static／build／pytest記録

| 実コマンド | 結果 |
|---|---|
| `<TEST_PYTHON> -m pytest -q tests/component/febio/test_required_quality_status.py tests/component/febio/test_mesh_refinement.py` | 18 passed、exit 0（251版E2E実施時の履歴。現行candidate最終full-suite／format後focused84とは別） |
| `python -m ruff format --check .` | exit 0、format-only commit `0e35ba4d`後、273 files |
| `python -m ruff check .` | exit 0、0 diagnostics |
| `python -m mypy src tests` | exit 0、216 files clean |
| `python scripts/scan_cae_data.py --root .` | exit 0、latest final candidate scan PASS、277 checked／276 tracked／0 diagnostics |
| `python -m build` | exit 0、sdist＋wheel生成 |
| `python -m build --sdist --wheel --outdir <COORDINATION>/mvp-20260921-final-build-sdist-0e35ba4` | exit 0。sdist SHA-256=`06dda3b8a1b70e3c7398a4e24081bfe84fc7dc0810d399db478c8977e20e55cb`、wheel SHA-256=`45f7163aff54724e8ab887d210ad3cc4999fc1addd97c9a1a914cd25e263381f`。prior native wheel SHA=`f978de...`は保持、112 members／uncompressed bytes equal、content differenceなし |
| default build receipt | `<COORDINATION>/mvp-20260921-final-build-default-0e35ba4/build-receipt.json`、`python -m build --outdir ...` exit 0。default sdist SHA-256=`9c11115684ed206295225c4e3e914b8760a868147203f67df4feb56e1b2271e7`、wheel SHA-256=`6f7fa91f4e6726959b273d55ab9ce75b19c5f18c9814c47b308a36a8825a1ead`、prior native wheel SHA-256=`f978de60803f70b9a5858a60bae185e0ed467949b0e062eafd2826a5e18b2cdd`／size=`404193`を保持し、112 members／uncompressed bytes equal |
| format equivalence receipt | `<COORDINATION>/mvp-20260921-full-suite-final-56e122b/format-equivalence-final.json`（format-only `0e35ba4d`、AST `include_attributes=False`同一、blank lineのみ） |

## 現行candidate sourceの変更ファイル

基準`af3a248`から本受入記録を含む最終候補まで33 files。製品source=`71c388f229dbfabc9fffb48c90c4ca9c1def5ee9`、test=`56e122b`、format=`0e35ba4d`、後続の文書変更を含む。251/b28の自動E2E証拠は履歴として保持し、現行candidateへ付け替えない。

```text
docs/cli-usage.md
docs/plans/2026-09-14-febio-cae-harness-greenfield-plan.md
docs/plans/mvp-handoff.md
docs/reviews/2026-09-20-mvp-planar-e2e.md
docs/specs/2026-09-14-febio-llm-cae-harness-design-v2.md
docs/specs/implementation-notes.md
src/febio_cae/adapters/geometry/_gmsh_runtime.py
src/febio_cae/adapters/geometry/inspection.py
src/febio_cae/adapters/geometry/preparation.py
src/febio_cae/adapters/meshing/native_surface.py
src/febio_cae/application/_adoption.py
src/febio_cae/application/_demo.py
src/febio_cae/application/_mesh_refinement.py
src/febio_cae/application/_preview.py
src/febio_cae/application/_required_quality.py
src/febio_cae/application/service.py
src/febio_cae/cli/case.py
src/febio_cae/cli/main.py
src/febio_cae/storage/mesh_quality.py
src/febio_cae/storage/preview.py
src/febio_cae/storage/registry.py
tests/component/application/test_comparison.py
tests/component/application/test_persistence_authority.py
tests/component/application/test_planar_preparation.py
tests/component/application/test_preview_flow.py
tests/component/application/test_required_numerical_quality.py
tests/component/cli/test_demo_cli.py
tests/component/febio/test_mesh_refinement.py
tests/component/febio/test_required_quality_status.py
tests/component/geometry/test_gmsh_preparation.py
tests/component/geometry/test_gmsh_runtime_identity.py
tests/component/geometry/test_native_primitive_adapter.py
tests/e2e/test_installed_synthetic.py
```

## 受理済みsourceの状態

`251dd12d14b79f7526773ae55bd08716f8e6f9c2`は上記source／testsを受理済みであり、format-only commit `0e35ba4d`はAST同一・blank lineのみ。ruff format／ruff check／mypy／scan／buildは最終記録でPASS。full-suiteは56e122bでPASS、manual 8とMVP全体判定は未完了である。
## full-suite records（historical interruption／postfix／final）

| 記録 | 状態 |
|---|---|
| historical attempt | `<COORDINATION>/mvp-20260921-full-suite-v251dd12/result.json`。wrapper duration `3600.12 s`、68%付近で`INTERRUPTED_TIMEOUT`、streamにfailure markers 2件のみ。final pytest summaryはなく、この履歴をPASS／final completed failure countへ読み替えない |
| invocation | `<ORIGINAL_VENV>\Scripts\python.exe -m pytest --basetemp <ROOT_PYTEST>/mvp-20260921-full-suite-v251dd12 -q --tb=short`（pyproject defaultのunit→component） |
| diagnostic collection | `<COORDINATION>/mvp-20260921-full-suite-v251dd12/collect-only.txt`は1798 tests。progress 1224 tests後の`27 dots+FF`で、対象は`tests/component/febio/test_required_numerical_quality.py::test_registered_source_local_refinement_consumer_routes_observed_status[factors2-logs2-UNVERIFIED]`と`tests/component/febio/test_required_numerical_quality.py::test_registered_source_local_missing_receipt_is_unverified`。これは診断であり、完了full-suite結果ではない |
| current postfix invocation | `<ORIGINAL_VENV>\Scripts\python.exe -m pytest --basetemp <ROOT_PYTEST>/mvp-20260921-full-suite-postfix-71c388f -vv --tb=short --junitxml <COORDINATION>/mvp-20260921-full-suite-postfix-71c388f/junit.xml`、PID=`42436`、console=`<COORDINATION>/mvp-20260921-full-suite-postfix-71c388f/pytest-console.log`、launch=`<COORDINATION>/mvp-20260921-full-suite-postfix-71c388f/launch-record.json`、state=`COMPLETED_NONPASS`、exit=`1`、`1797 passed / 1 failed / 3521.67 s` |
| prior postfix cause | 71c388f postfix標準full-suiteのsole failureは`tests/component/geometry/test_gmsh_runtime_identity.py::test_nested_optional_import_source_flow`の`RuntimeBindingError: finalize._dirty changed`。この非PASSは履歴として保持し、test-only修復後の56e122b最終標準full-suiteはPASS。runner setup `0e35ba4`はpytest欠落でcollection前停止、native／budget消費なし、corrected runner flow `0e35ba4-runnerfix`はinspect-nativeでinstalled Gmsh unavailable／geometryのexit 4、preparation／solver／Studio 0、retryなし。manual caseのbudget holdは変更しない |
| current final invocation | `<TEST_PYTHON> -m pytest --basetemp <ROOT_PYTEST>/mvp-20260921-full-suite-final-56e122b -vv --tb=short --junitxml <COORDINATION>/mvp-20260921-full-suite-final-56e122b/junit.xml`、test-only commit `56e122b`、source `71c388f` unchanged、PID=`6876`、receipt=`<COORDINATION>/mvp-20260921-full-suite-final-56e122b/launch-record.json`、state=`COMPLETED_PASS`、exit=`0`、`1798 passed / 0 failed / 3493.86 s` |
| runner setup attempt | `0e35ba4` unique smoke environment installed wheel/import successfully, then child exit `1` with `No module named pytest` before collection。receipt=`<COORDINATION>/mvp-20260921-final-auto-0e35ba4/runner-setup-failure.json`、status=`STOPPED_ENVIRONMENT_FAILURE_NO_RETRY`、JUnit／native attempts／budget consumptionなし、reinstall／relaunch／retryなし |
| native-enabled final flow | `0e35ba4` native environment (gmsh 4.15.2), final-accounting=`<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/final-accounting.json`、launch=`<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/launch-record.json`。pytest 1 passed／942.46 s／exit 0、22 stage全exit 0、dispatch inspection1／preparation1／solver2／Studio1、case=`case-80e5f42a5043`、coarse=`run-bad8952a7613`、candidate=`run-ff3035ba5048`、両run `SUCCEEDED`／必須5 numerical statuses `PASS`、baseline `COMPLETE`／Studio `LAUNCHED`、candidate comparison、same_mesh_reuse=true、comparison=`synthetic-comparison-aeda204cc5984c368e20ecd5ec689d22`、raw label=`NUMERICAL_GATE_PASSED_NOT_OVERALL`（mesh `UNVERIFIED`、原因推定なし）。manual 8は別必須記録、retryなし |
## 未取得／未完了の範囲
251版の自動T5（履歴）とnative-enabled final automated synthetic flowはPASSした。manual 8はpreparationで`ABORTED/BLOCKED`、T6 record failure、MVP全体判定は未完了である。71c388f postfix標準full-suiteはexit 1の非PASS、test-only commit `56e122b`の最終標準1798-test full-suiteはexit 0でPASS。runner setup `0e35ba4`はpytest欠落によるcollection前停止（native／budget消費なし、再試行なし）、missing-Gmsh corrected runner flow `0e35ba4-runnerfix`はinspect-native exit 4、`UNSUPPORTED_ENVIRONMENT`（installed Gmsh unavailable）で停止した。native-enabled final flowはgmsh 4.15.2で22 stage全exit 0、pytest 1 passed／942.46 s、両run `SUCCEEDED`／必須5 numerical statuses `PASS`、baseline `COMPLETE`／Studio `LAUNCHED`／candidate comparisonまで取得した。raw label `NUMERICAL_GATE_PASSED_NOT_OVERALL`は原因を推定せず保持し、manual 8がMVPで残る必須記録である。native final initial-state UI captureはlast-state／deformation／full mesh／`CONFIRMED`を証明しない。旧partial continuationはfull manual 8へ読み替えない。

| 判定対象 | 現在地 |
|---|---|
| 251版の自動E2E（履歴） | `PASS`（T5完了）。`same_mesh_reuse=true`、`NUMERICAL_GATE_PASSED_NOT_OVERALL` |
| final manual 8-step | `ABORTED/BLOCKED` at preparation (`b5fd...` `FAILED`／`StorageIntegrityError` wall 570 s)。T6 record failure。root cause `UNDETERMINED`、duplicate impact `UNKNOWN`。validate／freeze／solver／Studio／compare／retry／new caseは未実行。manual caseはretry／reset／代替caseを行わず、この状態を保持する |
| full pytest | historical attemptは`INTERRUPTED_TIMEOUT`／非PASS。71c388f postfix標準1798-test full-suiteはexit 1（`1797 passed / 1 failed / 3521.67 s`、sole failure=`test_nested_optional_import_source_flow`）で履歴保持。test-only commit `56e122b`の最終標準1798-test full-suiteは`PASS`（`1798 passed / 0 failed / 3493.86 s`）。runner setup `0e35ba4`はpytest欠落でcollection前停止、missing-Gmsh corrected runner flow `0e35ba4-runnerfix`はinspect-native exit 4、`UNSUPPORTED_ENVIRONMENT`（installed Gmsh unavailable、geometry、native qualification `UNVERIFIED`）、dispatch inspection1／preparation0／solver0／Studio0、retryなし。native-enabled final flow `0e35ba4`はpytest 1 passed／942.46 s／exit 0、22 stage全exit 0、dispatch inspection1／preparation1／solver2／Studio1、両run `SUCCEEDED`・必須5 numerical statuses `PASS`、baseline `COMPLETE`／candidate comparison、raw label `NUMERICAL_GATE_PASSED_NOT_OVERALL`（原因推定なし）。manual 8／MVP合格へ読み替えない |
| MVP | 自動8手順は`PASS`。本書作成時点（2026-09-21）ではMVP全体が旧manual caseのpreparation未完了のみで`UNVERIFIED/BLOCKED`であった。2026-09-22にmanual 8を新規caseで完遂し、MVP（計画書 §2）の必須記録はそろった（[manual 8実行記録](2026-09-22-mvp-manual-final.md)）。最終project完成は引き続き宣言しない。native candidateは受理対象として記録し、旧manual caseはretry／reset／代替caseを行わず、この状態を保持する |
| V2 integration | 未実施。V2は変更せず、dev branch publicationと分離し、明示的ユーザー指示待ち |

## 証拠参照

Git管理外の証拠は、次の記号rootで参照する。

| 記号 | 実体 |
|---|---|
| `<COORDINATION>` | 元のチェックアウト/.local/coordination |
| `<ROOT_PYTEST>` | 元のチェックアウト/.local/pytest-basetemp |
| `<WORKTREE_PROOFS>` | Orca開発ワークツリー/.local（docs-proofのみ） |

- `<COORDINATION>/mvp-20260920-live-admission-result.json`
- `<COORDINATION>/mvp-20260920-live-admission-review-result.json`
- `<COORDINATION>/mvp-20260920-origin-binding-fix-result.json`
- `<COORDINATION>/mvp-20260920-inspection-policy-correction.json`
- `<COORDINATION>/mvp-20260921-quality-id-review-result.json`
- `<ROOT_PYTEST>/mvp-20260921-final-e2e/test_installed_synthetic_cli_f0/installed-synthetic-attempt-report.json`（元のFAILED raw report）
- `<ROOT_PYTEST>/mvp-20260921-final-automated-v251dd12-rerun/test_installed_synthetic_cli_f0/installed-synthetic-attempt-report.json`（final automated raw report）
- `<COORDINATION>/mvp-20260921-final-automated-v251dd12/commands-ledger.jsonl`（22 stageのargv／exit）
- `<COORDINATION>/mvp-20260921-final-automated-v251dd12/pytest-console-rerun.log`（1 passed、1166.54 s、exit 0）
- `<COORDINATION>/mvp-20260921-case-budget-decision.json`
- `<COORDINATION>/mvp-20260921-intervening-acceptance-status.json`
- `<COORDINATION>/mvp-20260921-final-gates-v251dd12/gates-console.log`
- `<COORDINATION>/mvp-20260921-duplicate-preparation-incident.json`（調整incident。user authorizationではない）
- `<COORDINATION>/mvp-20260921-final-manual-incident-reconciliation.json`（read-only reconciliation、manual case ABORTED/BLOCKED at preparation）
- `<COORDINATION>/mvp-20260921-duplicate-preparation-incident-addendum.json`（terminal failure、root cause UNDETERMINED、no rerun/reset/budget/tolerance change）
- `<COORDINATION>/mvp-20260921-final-acceptance-accounting-v251dd12.json`（latest named accounting ledger、final automated 22 commands all exit 0）
- `<COORDINATION>/mvp-20260921-full-suite-v251dd12/result.json`（historical `INTERRUPTED_TIMEOUT`、非PASS）
- `<COORDINATION>/mvp-20260921-full-suite-v251dd12/collect-only.txt`（1798 tests、diagnostic collection）
- `<COORDINATION>/mvp-20260921-full-suite-postfix-71c388f/pytest-console.log`（修正版71c388fの標準full-suite、PID=`42436`、exit=`1`、`1797 passed / 1 failed / 3521.67 s`、非PASS）
- `<COORDINATION>/mvp-20260921-full-suite-postfix-71c388f/junit.xml` と `launch-record.json`（同一postfix full-suiteのdurable records）
- `<COORDINATION>/mvp-20260921-full-suite-final-56e122b/pytest-console.log`（test-only `56e122b`最終標準full-suite、PID=`6876`、exit=`0`、`1798 passed / 0 failed / 3493.86 s`）
- `<COORDINATION>/mvp-20260921-full-suite-final-56e122b/junit.xml` と `exit.json`（同一final full-suiteのdurable records）
- `<COORDINATION>/mvp-20260921-full-suite-final-56e122b/format-equivalence-final.json`（format-only `0e35ba4d`、AST `include_attributes=False`同一、blank lineのみ、ruff format／check PASS）
- `<COORDINATION>/mvp-20260921-final-auto-0e35ba4/runner-setup-failure.json`（runner setupのみ。pytest欠落でcollection前停止、native／case／budget activityなし、retryなし）
- `<COORDINATION>/mvp-20260921-final-auto-0e35ba4-runnerfix/pytest-console.log`（corrected runner real flow、inspect-native exit 4、`UNSUPPORTED_ENVIRONMENT`／installed Gmsh unavailable）
- `<COORDINATION>/mvp-20260921-final-auto-0e35ba4-runnerfix/junit.xml` と `exit.json`（同一corrected flowの1 failure、dispatch inspection1／preparation0／solver0／Studio0）
- `<COORDINATION>/mvp-20260921-final-auto-0e35ba4-runnerfix/launch-record.json`（same installed wheel/settings、`STOPPED_NATIVE_INSPECTION_UNSUPPORTED_ENVIRONMENT_NO_RETRY`、retryなし）
- `<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/final-accounting.json`（native-enabled final flow、source=`71c388f`／test=`56e122b`／wheel SHA-256=`f978de...`、case=`case-80e5f42a5043`、22 stage exit 0、dispatch inspection1／preparation1／solver2／Studio1、overall `NUMERICAL_GATE_PASSED_NOT_OVERALL`、same_mesh_reuse=true）
- `<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/pytest-console.log` と `junit.xml`／`exit.json`（pytest 1 passed／942.46 s／exit 0、JUnit failures/errors 0）
- `<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/launch-record.json`（native gmsh 4.15.2 env、same approved settings/input/physics/tolerances、no retry／manual hold）
- `<ROOT_PYTEST>/mvp-20260921-final-auto-native-0e35ba4/test_installed_synthetic_cli_f0/installed-synthetic-attempt-report.json`（case、両run `SUCCEEDED`／quality `PASS`、mesh `UNVERIFIED`、baseline `COMPLETE`／Studio `LAUNCHED`／candidate comparison、comparison ID）。comparisonはYoung's modulus `1e6→2e6 Pa`、force-z relative differences `[1,1,1,1,1,1]`、part displacement-z differences／relative differences `[0,0,0,0,0,0]`、same_mesh_reuse=true。candidate preview／Studio `CONFIRMED`は別の表示境界であり、このMVP自動gateの欠測扱いにはしない
- `<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/ui/baseline-studio-evidence.json` と `baseline-studio-state.png`（preview `preview-081d093dac1440999af8e6cb27011338`、Studio PID13152、initial display only、PNG SHA-256=`b23d070f0d2fa25a110c3135ed6c487869a7e0133c9ef8800c868486e0c87c92`）
- `<COORDINATION>/mvp-20260921-final-auto-native-0e35ba4/cumulative-accounting.json`（development cumulative: total 11 requests＝historical 9＋environment failure 1＋successful native flow 1、observed-success children 7、known solver 8／Studio 4、unknownは別保持）
- `<COORDINATION>/mvp-20260921-final-build-default-0e35ba4/build-receipt.json`（default build exit 0、prior native wheel SHA-256=`f978de...`、112 member names／uncompressed bytes equal）
- `<COORDINATION>/mvp-20260921-reservation-identity-readonly.json`（one committed gmsh row、sealed inputとlauncher/runtime bindingはprimary環境一致。caller/session pre-reservationとowner identityは`UNCERTAIN`）
- `<COORDINATION>/mvp-20260921-final-automated-ui-inspection.json`（existing capture recovery、new launchなし、UI_UNVERIFIED beyond initial state）
- `<COORDINATION>/mvp-20260921-final-automated-v251dd12/ui/studio-pid40100-state.png`（symbolic path／SHA-256=`ac3867de0a5cbb80d7607dbb73e3355b58c05c929ff4772887d2fe6115e7a8b1`のみ。Gitへコピーしない）
- `<COORDINATION>/mvp-20260921-duplicate-preparation-incident.raw.txt`
- `<COORDINATION>/mvp-20260921-final-manual-v251dd12/commands`（二つのCLI試行receipt）
- `<COORDINATION>/mvp-20260921-final-manual-v251dd12/case-root/preparation/b5fd255f1fb44b648069d1b6e026e7d8/state.json`
- `<ROOT_PYTEST>/mvp-20260921-final-automated-A/test_installed_synthetic_cli_f0/installed-synthetic-attempt-report.json`（distinct recovery A、inspection child process PID=`11908`／exit=`0`をraw fieldからread-only抽出）
- `<COORDINATION>/mvp-20260921-manual-continuation/step7-preview.json`
- `<COORDINATION>/mvp-20260921-manual-continuation-result.json`
- `<COORDINATION>/mvp-20260921-manual-continuation/step8-compare.json`
- `<COORDINATION>/mvp-20260921-manual-continuation/studio-results-xplt.png`（限定的UI観測、Gitへコピーしない）
- `<WORKTREE_PROOFS>/docs-proof-20260921-acceptance-docs/evidence.json`
- `<WORKTREE_PROOFS>/docs-proof-20260921-null-template/evidence.json`

56e122bの最終標準1798-test full-suiteはPASSした。runner setup `0e35ba4`（pytest欠落、collection前停止、native／budget消費なし）とmissing-Gmsh corrected flow `0e35ba4-runnerfix`（inspect-native exit 4、installed Gmsh unavailable、observed childなし）は履歴として保持する。native-enabled final flow `0e35ba4`はgmsh 4.15.2環境でpytest 1 passed／942.46 s、22 stage全exit 0、inspection1／preparation1／solver2／Studio1、両run `SUCCEEDED`／必須5 numerical statuses `PASS`、baseline `COMPLETE`／Studio `LAUNCHED`／candidate comparisonまで取得した。raw label `NUMERICAL_GATE_PASSED_NOT_OVERALL`は原因を推定せず記録し、mesh `UNVERIFIED`は別の境界である。旧manual `case-6294a0a9e02c`はretry／reset／代替caseなしで履歴として保持する。当時MVPで残る必須記録であったmanual 8は2026-09-22に新規caseの別flowで完遂し、同日ユーザー承認済みのV2 docs統合をff-onlyで実施した（pushは未実施、PM判断）。旧251/b28証拠、native final record、新wheel smoke、2026-09-22 manual recordを明確に分け、project doneは主張しない。dev branchの通常publicationはV2 integrationと別管理である。
