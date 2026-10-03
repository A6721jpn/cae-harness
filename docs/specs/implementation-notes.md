# FEBio CAEハーネス V2 — 実装ノート

作成日：2026-09-15／状態：現行実装の数値上限・構文規則・接続詳細の記録。

本書は[設計仕様書](2026-09-14-febio-llm-cae-harness-design-v2.md)から分離した、実装レベルの詳細記録である。設計仕様書が「製品が何を約束するか」を定め、本書は「現行コードがどの上限や規則によってそれを実現しているか」を記録する。ここに記載された数値や構文はコードの変更に追従して更新してよく、その変更が設計仕様書の約束（入出力、状態、終了コード、必須品質項目）を変更しない限り、設計仕様書の改訂は不要である。

## 1. 外部ツールの版と識別

- 公開調査および準備の候補は、Gmsh 4.15.2、同一の所有セッションに結合されたOCCT 7.8.1、STEP AP214である。
- 元のWindows wheelの識別値は SHA-256 `7b36083bb410fa27c5d0e052929d1a9844a5b09169d66017b72b41aabd49d711` である。この識別記録のみをもって幾何・数値品質を認定することはない。
- AP214は取り込み前にHEADERの `FILE_SCHEMA`、OCCTは同一所有セッションの `General.BuildInfo` により厳密に照合する。欠落、曖昧さ、不一致がある場合は形状操作の前に拒否し、別途導入されたOCCTで代用することはない。
- 汎用設定の `expected_occt_version`、`require_step_ap214`、`cpu_workers` は引き続き任意指定とするが、公開経路では上記の組み合わせを要求する。CPU指定は `General.NumThreads` に設定する。
- 実行時依存の取得では、公式ソース中のリテラルbool、bool名の分岐、import、および限定した入れ子のtry/exceptをソース順に解析する。未導入と証明できたimportだけが対応するフォールバックを選択する。パス・コード・モジュール同一性の認証失敗は、任意依存の未導入として無視しない。
- vendorの`gmsh.py`にはNumPyから`weakref.finalize`へ分岐する任意経路があるが、MVP環境ではNumPy未導入のためruntime／native分岐は`UNVERIFIED`である。NumPy不在やproduction no-weakref pathを主張せず、選択されなかった`backports.weakref`を必須化しない。product guardは変更しない。`try_numpy=True`、`use_numpy=False`、`numpy`／`weakreffinalize`の未束縛状態を、初期live状態と再検証時に照合する。任意の式を実行する評価器ではなく、未対応のimport制御フローは拒否する。このソース分岐の検証だけを、完全な実行時認証や実Gmsh操作の合格証拠にはしない。
- 標準ライブラリの組み込み関数の別名は、正規ソースのimport元と実際の定義元の同一性を確認する。factoryが返す入れ子関数の公開名は、正規ソース内の宣言・return・引数なしの直接代入との対応を確認し、関数名の末尾一致だけでは許可しない。propertyの各accessorはデコレーター開始行とgetter／setter／deleterの役割を用いて個別のコンパイル済みコードへ照合し、同じqualnameの最初のコードで代用しない。いずれも既存のコード・closure・globals・live状態の検証を維持する。
- 取り込んだ関数の再公開は、正規ソースのimport対応（別名・wildcardを含む）で取り込み元を解決してから定義元の関数を検証する。取り込み先モジュール自身の定義と誤分類しない。公開名と実体の対応、および `ctypes` の既知の結び付けは同一性で照合し、別の認証済み関数への差し替えも許容しない。
- 既定の対応表は `src/febio_cae/resources/planar_default_bundle.json`（約44 KB）。旧承認バンドル（SHA-256 `f5f5ce51...`、604,962バイト、Git追跡外）から3プロファイルとメッシュ品質基準を抽出し、証拠参照を1件の出所メモ `planar-default-provenance` に付け替えたもの。読込器の `executable_digest` は `sha256("febio-cae-xplt-reader 0.1.0")` の版ベース識別で、ソースのバイト列とは照合しない。外部バンドルを `--...

## 2. STEP調査（`case inspect --native`）

- 所有子プロセスを1回のみ実行し、メッシュ生成や再試行は行わない。
- 実行時間は正の有限値とし、既定値および上限値を600秒とする。CPU数は利用可能数以下の正の整数とする。メモリ使用量は開始時の利用可能物理メモリ量の80%以下、かつ総物理メモリ量以下とする。数値設定において、真偽値、非有限値、ゼロ、上限超過は受け付けない。すべての操作に対して単一の期限を適用する。
- 応答は読み込み前にサイズを検査し、`min(16 MiB, 有効メモリのバイト数 // 16)` 以下であることを確認した上で、型、件数、有限値、ハッシュの全体検証を通過させる。上限超過時にデータを切り詰めて返却することはない。
- 子プロセスでは通常の `GmshOCCBackend` および `StepGeometryMeshAdapter` の形状処理を用い、準備用の平面上書きは適用しない。同一セッション内での測定処理のみを追加した派生クラスは許容する。呼び出し側が指定したツールの実体、版、ハッシュ、調査報告は信頼しない。
- 処理の開始前および返却前に登録済みSTEPの内容と草案世代を検証し、子プロセスの処理中はケースロックを保持しない。草案、世代、凍結版、準備記録、権威ある登録情報を変更することはせず、一時的な所有記録のみを残す。
- 面などの各IDは元の入力と調査に帰属するものであり、恒久的な意味IDではない。
- 終了コード：世代競合は8、入力破損は6、ツールやAP214の不一致および時間切れは4とする。

## 3. 平面準備（`case prepare-planar`）

- 既定の上限は600秒、生成は1回、四面体100,000要素、250,000節点とする。明示的な有限値への上限変更は可能であるが、自動的な粗分割、無制限化、隠れた再試行は行わない。
- CPUは利用可能数と明示上限の小さい方、メモリは開始時の利用可能量の80%以下かつ総量以下とし、期限とともに子プロセスで強制する。
- 初回に限り、内部正規化において形状・調査と対応する部品選択の生成ハッシュにおける `null` を許容し、今回の子プロセスによる調査結果から補完する。非null値の不一致は拒否する。
- 生成側が `PREPARING / FAILED / PREPARED` を発行し、入力、仕様、根拠、世代、ツール実体、調査、メッシュ、生成条件、出力を結び付けて原子的に公開する。既存のロック、CAS、生成記録を利用し、汎用的な複数DB取引層は追加しない。
- 公開準備経路では、ケース全体でメッシュ生成3回およびFEBio実行4回を起動前に永続予約し、失敗、中断、新規版の作成が発生した場合でも消費枠を復元しない。旧登録デモにおける、メッシュ追加なし・FEBio2回・Studio1回という別契約は維持する。
- Tet10については、GmshとFEBioの間で節点および面節点の順序を明示的に変換し、要素ID、法線、Jacobian、退化、集合、曲面近似、接触の独立性を検証する。
- メッシュのキャッシュキーには、形状、治具生成条件、分割領域、メッシュ設定、変換器および外部ツールの版を含める。再利用前には内容と領域の対応関係を検証する。

### 対応表の限定範囲

能力名 `febio.scope.planar_linear_frictionless_fixed_xyz` を持つ対応表は、準備予約前およびコンパイル前に以下の条件を強制する：等方線形弾性、摩擦なし、直方体治具1つ、`AsPlaced`、World座標系における−Z方向運動、座標変換を伴わないXYZ完全固定支持、治具のx/y/rx/ry/rz固定およびz指定、0〜1秒を0.1秒刻みとする10増分、適応増分および再試行なし。タグのない対応表は従来の契約を維持する。

## 4. 実行と出力固定

- 入力を試行専用領域へ固定し、実行ファイルの絶対パス、版、ハッシュ、関連DLL・プラグイン、コンパイラ、設定、引数配列、作業ディレクトリ、スレッド数を記録する。シェル文字列は実行せず、外部参照と出力先を登録済み入力の範囲内に制限する。限定品質経路では、認定済み実行ファイルおよび全DLLの実バイトを起動前に照合する。
- PID、生成時刻、所有世代、Windows Job Object等により所有プロセス群を識別する。正常終了、取り消し、時間切れのいずれの場合も、子孫プロセスおよび書き込み処理の終了後に結果を検証する。他のFEBioやStudioを終了させることはない。

| 固定するファイル | 初期上限・規則 |
|---|---|
| `input/case.feb` | 32 MiB。登録済み入力一覧から役割・保持先・サイズ・ハッシュを解決し、結果側の入力ハッシュと照合 |
| `output/results.xplt` | 必須、128 MiB。読み込みブロック累計900,000、入れ子を含め1ブロック16 MiB |
| `output/solver.log` | 任意、8 MiB。存在する空ファイルも保存。不正、超過、読み込み不能、改変は完全性エラー |

結果一覧には固定済み出力のみを列挙し、入力を重複して登録しない。すべての出力を検証した上で、閉じた集合として登録する。真にログが存在しない場合のみを任意扱いとする。非ゼロ終了時や取り消し時におけるログの回収は未完了項目である。

## 5. 数値品質の判定処理

### 初期判定範囲の各処理

- `planar_contact`：両面の変位および選択した2次面を用い、初期干渉、隙間、食い込み、力の下限、接触区間の明示基準を確認する。正の治具力のみをもって合格とすることはない。
- `motion_support_contact_fidelity`：全軌跡、剛体位置、登録支持成分を、運動誤差および支持変位限界と照合する。
- `quasistatic_equilibrium`：正規化済み支持反力と生の剛体作用力を、各保存状態自身の絶対許容差および相対許容差で照合する。
- `solver_residual`：採用された全増分と残差を確認する。印字精度は保守的に扱い、各接触反復における要求値の印字区間に、固定された `tolerance` またはSI単位の `gaptol` が含まれることを要求する。
- `mesh_dependence`：明示された、減少するTet10の全体サイズ3段階、相対力差限界、力の下限を用いる。2回の細分化によって部品要素数が増加し、実測最大辺が減少することを確認した上で、双方の力履歴差を検証する。材料変更への継承は、同一の線形弾性条件、同一の最細メッシュ、明示的な弾性率正規化比較を行う場合に限定する。

公開 `required_quality` は、版・仕様、メッシュ、対応表、試行・入力・結果、算術評価のIDとハッシュから導出する。再計算した算術評価が登録済みの評価と厳密に一致することを要求し、存在しない場合は未検証、不一致や改変がある場合は完全性エラーとする。読み取り操作によって不足している評価を登録することはない。

### 任意の符号付き力和 `signed_force_sum`

同一のスカラー成分および座標系、全保存状態における同一順序、力の次元、ならびに `aggregation_id=sum` を明示した評価のみを使用する。要求の別名を含め、出力配置と実体IDの組み合わせによる寄与の重複は拒否する。各状態の符号付き和から `max_t |Σ_i F_i(t)|` をNで求め、明示された単一の有限・非負かつ力次元の閾値 `max_value` と比較する。閾値以下は合格、超過は不合格とし、欠測、非有限値、不一致、重複は未検証とする。絶対値の和の算出、状態間での相殺、暗黙の符号補正や軸変換、ゼロ補完は行わない。既存の `peak_abs_value` は変更しない。

### ログに印字された残差 `reported_solver_norms`

登録済みのログ、入力、実行ファイル、版、対応表、ケース、メッシュに結び付く観測報告である。生のブロック順序ならびに文字列・バイト位置を保持し、採用が明白な最終の非線形反復と後続の接触反復を選択する。期待される増分は入力制御から求め、XPLTの保存数で代用することはない。

初期の受理範囲は、FEBio 4.12.0、`solid/static`、0.1×10増分による0〜1の範囲、接触1組の `sliding-elastic`、明示的かつ整合する有効制御、`min_residual=0`、および既知の上限値とする。未知の構文、適応増分や再試行、複数組の接触、不明な上限や対応関係は未検証とする。`max_ups` による再形成を再試行と誤認してはならない。`CONTACT INTERFACE DATA` は前文中、唯一の接触宣言および増分より前に一度だけ許容される見出しであり、接触宣言そのものではない。

非線形反復行は `INITIAL/CURRENT/REQUIRED`、接触反復行は `CURRENT/REQUIRED` を保持し、該当行の要求値を使用する。有限かつ非負のCURRENTと正のREQUIREDを十進数で厳密に比較し、前者が小さい場合は報告上の合格、大きい場合は不合格とする。同値、要求値がゼロ、非有限値、無効制御、判定の短絡、未対応の場合は未検証とする。推定単位、内部計算式、微小な許容差を勝手に追加せず、`Maximum gap` を物理的な食い込みや平均L2へと読み替えることはしない。

信頼できる採用済みの最終行における不合格は、全体の不合格に反映する。正当な途中終了であれば先行する採用不合格を保持するが、記録の矛盾や不正な構文はその系列の信頼性を損なわせる。非最終行、不採用行、未確認行から確定的な不合格を判定することはない。

| 許容する追加数値設定 | 条件 |
|---|---|
| 接触 `minaug`／`maxaug` | 非負整数／正整数。両方ある場合は `minaug ≤ maxaug` |
| `reform_augment` | 真偽値 |
| 正の `max_ups` | `solver/qn_method type=BFGS` の正整数 |
| 非対称完全Newton | 整数の `max_ups=0` と `symmetric_stiffness=0` の組のみ。`Broyden` 内に前者、`solver` 内に後者を出力 |

整数項目における真偽値、片方のみのゼロ、その他の方式や対称性の組み合わせは拒否する。省略時および正値BFGSの既存動作を維持し、既定値は追加しない。

## 6. XPLT読込

XPLTのヘッダ、辞書、メッシュ、状態、変数、圧縮、配置および型を、対応する版ごとに検証する。未対応の形式は拒否し、積分点値、節点値、平滑化値を区別する。治具の移動および力は、検証済みの同一試行における数値履歴から取得し、欠測をゼロで補完することはない。登録読込器が完全な辞書を必要とする場合、コンパイラは表示要求に含まれないものを含むすべての変数を出力する。

## 7. Studio起動と観測（`case preview`）

`--window-id` を省略したMVP経路は、成功した実行のmanifestから対象XPLTを解決し、既存の読取ハンドルでXPLTとStudio実行ファイルを保持して内容ハッシュを検証する。ケース領域外を作業ディレクトリとする引数配列・`shell=False` の起動後、既存のpreviewストアへ `LAUNCHED`、PID、起動時刻、実行ファイルのパス・ハッシュ・取得可能な版を保存する。版メタデータがない場合は理由付き `UNVERIFIED` とし、Studioの終了を待たない。起動失敗を成功に置き換えず、`preview-status` は現在の結果と必須品質を再検証する。

`--window-id` を指定した経路は既存の外部Studioプロセスおよびウィンドウを特定し、XPLTハッシュと表示要求に結び付く一回限りの観測要求を発行する。`CONFIRMED` においては、独立した操作者による対象ファイル、Studioの版、最終状態、変数・成分・座標・単位の実際の表示確認が必要となる。既存セッションおよび一回限りの識別子と合致する記録、ならびに要求後に撮影されたPNGを有限の期限内に標準入力PIPE経由で受け取り、現在のXPLTハッシュと照合する。通信仕様は `src/febio_cae/cli/preview.py`、照合は `src/febio_cae/application/_preview.py` に従う。

公開ブリッジは `scripts/observe_preview.py`（標準ライブラリのみ、Windows用）である。installed `febio-cae` のpublic commandをstdin/stdout PIPE付きで1回起動し、実際の `PREVIEW_REQUESTED` 行をそのまま `request.json` に原子的に公開する。`response.json` を1回だけ読み、内容を改変せず末尾改行を補ってstdinへ渡す。`stdout.jsonl` と端末には子の実出力を保存・表示し、通常は子の終了コードを返す。既存ディレクトリの再利用と要求前の応答を拒否し、helperエラーは7。子プロセスのみを有限時間で回収し、Studioは起動・終了しない。JSON・nonce・対象・表示値・PNGの照合はinstalled CLIの既存検証に委ねる。

### 独立操作者（M2ではPM）の手順

1. 承認済みの新M2 flowに対し、公開 `case preview`（`--window-id` 無し）等で予算内のStudio **1回**を起動する。対象XPLTと表示変数・成分・World座標・単位・最終状態を実desktop UIで設定し、Studioの版と実ウィンドウの **HWND（PIDではない）** を観測する。ここは要求発行前に済ませる。旧caseは操作しない。
2. 別の端末／監督プロセスで下記を起動する。`<EXCHANGE>` はOS一時領域等の**未作成ディレクトリ**で、case／製品state／Git管理対象の外に置く。helperはソースpackageをimportせず、指定したinstalled executableを使う（wheel再build不要）。

   ```text
   python scripts/observe_preview.py --cli "<INSTALLED_FEBIO_CAE_EXE>" --exchange-dir "<EXCHANGE>" --state-dir "<STATE_DIR>" --case-id "<CASE_ID>" --manifest-id "<MANIFEST_ID>" --studio "<STUDIO_EXE>" --window-id <HWND> --timeout 120
   ```

3. `<EXCHANGE>/request.json` 出現後に内容を読む。これは `{"status":"PREVIEW_REQUESTED","request":{...},"remaining_seconds":...}` という実CLI出力で、`request` 内に `receipt`、`binding`、`issued_ns` がある。**期限は観測処理開始から最大120秒**（CLI既定30秒、helper既定120秒）、残り時間は実出力の `remaining_seconds`。準備・ハッシュ読込にも時間を使うため出現後120秒ではない。helperの子起動／終了用30秒余裕は観測期限を延長しない。
4. PMは要求後に実画面を再確認して新しいPNGを作成する（既存画像のコピー不可、20 MiB以下）。PNGはGit外の新しいファイルに置き、必要な表示値と出所を確認する。応答は**UTF-8・BOM無し・JSON object 1行・改行込み64 KiB以内**で以下の全項目を含める。要求値を観測値として自動転記せず、不明・不一致なら送信せず停止する。

   | 応答キー | 根拠・型 |
   |---|---|
   | `preview_id` | 今回の `request.receipt.receipt_id`（文字列） |
   | `request_nonce` | 今回の `request.binding.nonce`（文字列、一回限りの相関ID） |
   | `manifest_id` | 今回の対象manifest ID（文字列） |
   | `loaded_file`, `xplt_sha256` | 実際に開いたXPLTの絶対パスとSHA-256。要求の `binding.source_path`／`xplt_digest` と一致が必要 |
   | `studio` | 実際のStudioの `tool_id`, `version`, `executable_digest` を含むobject。要求のidentityと一致が必要だが版を未観測のままコピーしない |
   | `session` | 実プロセスの `process_id`（整数）、`process_start_marker`（文字列）、`window_id`（整数）のobject。要求のsessionを実窓・プロセスと照合して返す |
   | `observed_state_id`, `observed_time_s` | 実際に表示した最終状態番号（整数）と時刻（数値） |
   | `observed_variable`, `observed_component`, `observed_frame`, `observed_unit` | 独立観測した表示変数・成分・座標・単位（文字列）。`required_*` は確認対象であって観測の証拠ではない |
   | `observer` | 独立操作者の識別・帰属（空でない文字列） |
   | `capture_path` | 要求後に撮影した新PNGの絶対パス（文字列） |

5. 書きかけを読ませないため、同じ交換ディレクトリの `response.tmp` へ完全な1行を保存し、**同一ディレクトリ内renameで `response.json` として公開**する。ファイルを直接追記・上書きしない。helperは応答1件を送信して閉じ、子CLIが既存ストアへ検証済み証拠を登録する。helperがcaseへ直接書き込むことはない。
6. 端末終了コードと `stdout.jsonl` の最終応答を記録する。`preview_status=CONFIRMED` と必要な品質状態を確認し、必要なら公開 `case preview-status` で再検証する。タイムアウト・不一致・子エラーは成功ではなく、M2の失敗停止／再試行0に従う。交換ファイル・PNG・実パスはGitに入れず、実行証拠として外部に保持する。

制約：helperは画面操作、スクリーンショット撮影、表示値の推定、バージョンの補完、`CONFIRMED`生成を一切行わない。実UI観測は別途必要であり、fixture試験はその代わりにならない。MVPの表示確認は従来の `LAUNCHED` 契約を保持し、追加MVP GUI criterionへは拡張しない。

## 8. LLM接続（OpenAI Responses）

- 設定キーは `provider, model, key_env, budget, input_tokens, output_tokens, socket_seconds` とする。モデル名および鍵の環境変数名に既定値はなく、モデルを自動代替することはない。鍵はメモリ内で解決し、引数、ファイル、ログ、エラーへは出力しない。
- `budget` は `max_elapsed, max_attempts, cpu_workers, max_llm_calls, max_llm_tokens` である。これは各操作とその再試行のための予算であり、ケース全体の解析予約とは区別される。草案予算が未設定の間は設定予算を記録して使用し、以降は双方の上限の小さい方を適用する。明示的なゼロ指定は無効化とする。
- 入力数計測1回および生成最大1回の計2回の呼び出しを永続予約する。入力上限Iおよび出力上限Oに対して `2I+O` を確保するが、計測分のIはローカルの上限であり、実際の課金量ではない。有効な生成使用量で裏付けられる容量のみを解放し、不明分は保持する。自動再送、代替、暗黙の再試行は行わない。
- 操作ID、文脈、指示、設定、要求、スキーマ、状態、使用量、返却されたモデルを記録する。同一IDで内容が異なる場合は競合とし、同一内容であれば記録済みの結果または不確定状態を返し、再送や再適用は行わない。通信中はケースロックを解放し、最終CASが失敗した場合でも使用量を記録する。

| 制限 | 初期契約 |
|---|---|
| 保持出典 | 最大16件、合計64 KiB。超過は入力エラーとし、切り詰めない |
| 通信 | 要求・応答は各256 KiB、子プロセス用メモリ256 MiB、CPU 1 |
| 時間 | 経過・通信期限は正の有限値。単一の全体期限における残余時間を各子プロセスへ渡す |
| 出力 | トークン上限は16以上。計測値超過の場合は生成しない |

標準HTTPSで固定された `api.openai.com/v1` を使用し、リダイレクトや透過的な再試行は行わない。計測と生成の間でモデル、入力、指示、厳密に閉じたスキーマを一致させ、生成時は `store/background/stream=false` とし、ツールや履歴IDは使用しない。許容された推論メタデータと、完了した単一のアシスタント提案本文のみを受理する。模擬通信は非公開の試験差し込みによって行い、公開の偽提供者オプションは設けない。

物理的根拠の照合は、一行全体の `field = value` または `field: value` に限定する。NFKC正規化の実施後、値、単位、対象ボディ、ケース範囲を独立して照合する。受理する項目は、材料モデル（`isotropic_linear_elastic` / `compressible_neo_hookean`）、`material.youngs_modulus`（値と単位）、`material.poisson_ratio`（無次元単位 `1`）、ひずみ・速度の適用性（`applicable / applies / 適用可`）、および `support = adopt revision-id.support` 形式による登録済み構成要素の明示的な採用とする。

## 9. 独立した手動基準解析の検証境界（試験専用）

`assess_reported_norm_observations(source, log, *, policy, profile, invocation)` はアダプター内の共通処理として、解決済みファイル、固定数値方針・運動終端、対応表、実行ファイル・引数・プロセス記録を受け取る。公開処理と同一の受理条件、8 MiB・ASCIIログ、4.12.0、入力・表示設定・実行引数、10増分、構文・上限、採用反復選択、十進比較を使用する。共通処理自体は、OSレベルの所有権、証明の真正性、現在の別名を認定することはない。

独立した手動基準解析用の検証環境に限り、`ExecutionBundle.case_id` に名前空間付き試験ID、`revision_id` に実行前の不変な試験仕様版、`spec_digest` に完全な正規化試験仕様のSHA-256を使用できる。ローカルの対応表参照も名前空間付きの不変な承認記録へと結び付ける。実行後の記録において仕様・入力一覧、実際の入力・メッシュ・プロセス・出力、所有世代と子孫プロセスの終了を個別に検証し、手動試験用であることを明記する。これらの記録を公開ケース保存、コンパイラ、実行管理、品質判定へ投入することはない。手動試験における算術合格は、公開品質や実機適合性の合格を意味するものではない。

## 10. 正規化・ハッシュ

正規化は共通実装によって行い、UTF-8、空白なしJSON、キーおよび順不同集合の整列、型別の数値表現を固定する。履歴配列の順序は保持し、NaN、無限大、重複IDは拒否する。仕様ハッシュには根拠とスキーマ版を含め、自己ハッシュおよび作成時刻は除外する。内容の同一性と数値許容差は明確に区別する。
## 10.1 現在地と状態の区別（2026-09-21）

`INSPECTED`はSTEP観測、`PROVISIONED`は対応表登録、`PREPARED`はメッシュ・治具生成記録、`SUCCEEDED`は実行結果の正常固定、`LAUNCHED`はStudio起動receipt、`COMPLETE`は品質と表示条件を含む終端を示す。前段の状態を後段の証拠へ読み替えず、`native_qualification`・科学的妥当性・実物適用性の`UNVERIFIED`も独立に保持する。

| 項目 | authoritative status |
|---|---|
| 実E2E実施版／修正版regression | current source=`71c388f229dbfabc9fffb48c90c4ca9c1def5ee9`、test-only=`56e122b`、format-only=`0e35ba4d`。focused／static／build／scanはcanonical reviewの最終記録でPASS。251/b28、71/c180、過去1797/1は履歴として保持する |
| acceptance evidence | [canonical review](../reviews/2026-09-20-mvp-planar-e2e.md)。native final wheel SHA-256=`f978de60803f70b9a5858a60bae185e0ed467949b0e062eafd2826a5e18b2cdd`／size=`404193`が現行candidate。default build receipt、format equivalence receipt、native final launch／accountingを同reviewに固定し、current `case-80e5f42a5043`はgmsh 4.15.2、22 stage全exit 0、pytest 1 passed／942.46 s、両run `SUCCEEDED`／必須5 numerical statuses `PASS`、baseline `COMPLETE`／Studio `LAUNCHED`／candidate comparisonまで取得。比較値はforce-z relative differences `[1,1,1,1,1,1]`、displacement-z differences／relative differences `[0,0,0,0,0,0]`。raw label／mesh `UNVERIFIED`は原因推定なしで別境界として保持する |
| pending | native-enabled final flowの自動gateはPASS済みで、合格済みsource＋native candidateは受理対象として記録する。MVPで残る必須記録はmanual 8、manual `case-6294a0a9e02c`はretry／reset／代替caseなし。今回の追加実行は不要、manual次操作はbudget判断待ち、V2 integrationは明示的ユーザー指示待ち。詳細は[canonical review](../reviews/2026-09-20-mvp-planar-e2e.md) |

source-local criterion ID collision fixは、canonical global mesh producerにsource-local metricがない場合だけoptional `UNVERIFIED` allowanceを適用し、明示されたsource-local row（`criterion_id=mesh_dependence`でも）はstrictに扱う。V2は変更せず、明示的ユーザー指示があるまで統合しない。


## 11. `orca/acceptance-integration` で追加された実装（合成検証のみ、MVP後）

- **Gmsh実行時識別の認証**（`adapters/geometry/_gmsh_runtime.py`）：公開調査・準備は、Gmsh APIを呼ぶ前に親で `gmsh-runtime-identity-v1`（インタープリタ、配布モジュール、ネイティブライブラリ、`pyvenv.cfg` の解決パス・サイズ・SHA-256）を取得し、子で実際にマップされたライブラリと照合する。モジュール名や版文字列だけでは読込コードの証拠にしない。不一致・欠落は失敗として閉じる。
- **ソースローカル細分化**：`LocalRefinement` に任意の `SourceLocalRefinementBall`（ソースローカル座標の中心 `Point3` と正の半径）を追加。`WholeBodyRule` を持つボディにだけ許し、配置前の座標で解釈する。重なる球では最小の要求サイズが勝ち、宣言した全体サイズが外側の目標、遷移は決定的に記録する。`source_local_mesh_dependence` は `coarse_size / refined_size / fine_size` と力差限界・力下限を局所球に適用し、全体サイズは固定。各球に3本以上の角節点辺があり、2回の細分化で局所辺数増加・最大辺長減少・要素数増加を要求する。
- **曲面ネイティブ生成**（`adapters/meshing/native_surface.py`）：球・円柱の生成を登録済み形状アダプターへ接続。既存のアフィン多面体近似の基準を曲面Tet10の証明として流用しない。配置は下流で1回だけ適用する。
- **Windows CPUアフィニティ**：ネイティブ経路では宣言したCPU割当をJob Objectのアフィニティマスクで、子の割当・再開前に強制する。マスクは親プロセスの許可アフィニティの部分集合。空・取得不能・設定失敗は起動拒否。環境変数のスレッド指定は制御であってOS上の証拠ではない。
- **運動スケール符号校正**：低ペナルティ平面符号プローブの終端重なり区間 `[1e-6, 1.1e-5] m` を、中間状態では宣言した接近変位÷最終値で両端をスケールする。凍結した運動スケジュールだけを使い、実測値・フィット値は使わない。calibration05 は最初の接近が約 `1e-6 m` で下限に余裕がないため不合格のまま。
- **符号付き干渉区間**：正準Tri6パッチと球・有限円柱・中心直方体の最小符号付きユークリッド距離を、既存の二進・Bernstein算術で外向き区間として包囲する。内側は負。要求精度で細分し、作業上限で広い区間を返すことがある。閾値が区間に交差する場合は消費側で `UNVERIFIED`。

## 12. 独立した単一ソリッド静荷重（`static-load`）

既存の接触 `CaseSpec` と `case run` は変更しない。次の公開経路は、STEPの単一閉ソリッド、CAD面のWorld XYZ固定、CAD曲線ごとの**合計力**、等方線形弾性だけを扱う。重力、接触、剛体治具、密度荷重を追加しない。実モデルと保存rootはGit外に置く。

```text
febio-cae static-load prepare --root "<STATIC_ROOT>" --cad "<ORIGINAL_STEP>" --request "<STATIC_REQUEST_JSON>"
febio-cae static-load run --root "<STATIC_ROOT>" --solver "<FEBIO4_EXE>"
febio-cae static-load status --root "<STATIC_ROOT>"
```

全コマンドはJSONを返す。`prepare` と `run` は別の明示操作であり、1 rootにつき準備1回・solver試行1回だけ予約する。失敗や中断後に同じrootを再試行しない。元STEPは読み取りのみ、SHA-256を開始前と準備後で照合し、rootには検証済みsnapshotを保存する。

`--json` を受理し、指定の有無によらず `schema_version=1` のJSONを返す。終了コードは共通契約に従い、入力不正2、環境/未対応4、solver実行失敗5、出力完全性/数値品質不合格6、中断/取消7、競合/一回限り予約の再利用8。状態読込の終了0は解析成功を意味しない。`budget.memory_bytes` はネイティブ準備のJob Objectと入力・出力サイズに適用する上限であり、既存RunnerAdapterのsolverヒープ上限を新設するものではない。

native子プロセスは、専用の有界な通常ファイルへ失敗分類と元の理由を記録する。選択CAD面/曲線の不在、荷重と支持の重複、ソース不一致などの入力不正は2、実行環境・native失敗・期限超過は4、成功応答の欠落/破損は6とする。失敗記録を信用できない場合は元の環境失敗を保持する。使用中leaseまたは既存run予約は、準備読込・profile保存・bundle stagingより先に8を返す。

`status` は保存rootを新規作成しない。不在rootは入力不正2、既存の公開記録の破損・必須キー欠落・sealed出力欠落は完全性6としてJSONを返す。


要求schema（SHAは実ソースの値に置換する。下記アルゴリズム設定は構文例であり、任意CADの成功を保証しない）：

```json
{
  "schema_version": "1",
  "source_sha256": "<LOWERCASE_SOURCE_SHA256>",
  "fixed": {"face_ids": [1], "components": ["x", "y", "z"], "frame": "World"},
  "loads": [
    {"curve_id": 12, "semantics": "TOTAL", "unit": "N", "frame": "World", "vector": [0, 0, -10]},
    {"curve_id": 14, "semantics": "TOTAL", "unit": "N", "frame": "World", "vector": [0, 0, -10]}
  ],
  "material": {
    "model": "isotropic_linear_elastic",
    "youngs_modulus": {"value": 68000000000, "unit": "Pa"},
    "poisson_ratio": {"value": 0.33, "unit": "1"}
  },
  "mesh": {
    "global_size": {"value": 2, "unit": "mm"},
    "native_coordinate_unit": "MM",
    "algorithm_2d": 5,
    "algorithm_3d": 1,
    "curvature_points": 0,
    "max_nodes": 1000000,
    "max_elements": 1000000
  },
  "budget": {
    "cpu_workers": 1,
    "mesh_wall_seconds": 600,
    "solver_wall_seconds": 600,
    "memory_bytes": 1073741824
  }
}
```

面・曲線IDは認証済みGmsh/OCC import後のelementary entity tagであり、STEPレコード番号ではない。固定集合は選択したTri6面の全角節点・中間節点。選択曲線は同一所有native session内のline3を保存し、全外表面の二次辺と一致することを確認する。荷重と固定の節点共有は角・中間のいずれも拒否する。

固定XYZは既存compilerと同じ `bc type="prescribed displacement"` を各軸に出力し、`dof`、`<value lc="1">0</value>`、`<relative>0</relative>` を指定する。同じゼロDirichlet条件のまま、FEBioの反力記録対象となる自由度を確保する。単なる `zero displacement` で反力fieldがゼロとなった実行を釣り合い合格と扱わず、支持反力を実出力で検査する。

line3の形状関数と曲線Jacobianを3点Gauss積分し、**各曲線ごと**に積分長で正規化して要求TOTAL力にする。直線1要素なら端点各1/6・中間2/3。共有節点の寄与と符号を合算する。`N/mm`等の線密度や曖昧な意味指定を受理しない。FEBioにはsingleton NodeSetと `nodal_load type="nodal_force"` の3成分 `value lc="1"` を出力する。

`M` は従来native metre挙動、`MM` はこの静荷重backendだけの明示native millimetre政策。アルゴリズムと曲率設定を含む要求全体をrecipe digestに結び付ける。最終MeshArtifactは常にSIで、既存のTet10置換 `(0,1,2,3,4,5,6,7,9,8)`、全二次写像の正値証明、外表面完全被覆を再利用する。自動fallback、治癒、面削除は行わない。支持CAD面の実測geometryとnative runtime identityも準備記録に保持する。

`mesh.second_order_linear` は任意の真偽値（省略時false）。trueは標準 `Mesh.SecondOrderLinear=1` を明示して中間節点を直線補間し、**変位は二次Tet10・幾何はアフィン**とする。CADデータの修復/削除ではなく、CAD曲面への正確な投影を主張しない。falseは従来の曲面投影。いずれも全要素の正値証明、全外面被覆、CAD面/荷重曲線対応を省略せず、幾何政策をrecipe digestへ固定する。CAD近似とメッシュ依存性は独立に `UNVERIFIED` とする。構文の根拠：[Gmsh 4.15.2 mesh options](https://gmsh.info/doc/texinfo/)。

ネイティブ準備のstdout/stderrとGmsh出力を保存する。大規模メッシュの固定前には不要なbackend投影・照合辞書とSTEP hex payloadを解放し、検査済みエンティティを共有する。調査用の面メッシュexport・logger補助処理は製品経路に残さず、元のnative例外をそのまま伝える。CPU/時間/メモリー上限を自動拡張しない。


`mesh.local_refinements` は任意の配列（省略時空、最大64件）。各項目は既存 `SourceLocalRefinementBall` の `region` と単位付き `size` を持つ：

```json
{"region":{"schema_version":"1","kind":"source_local_ball","center":{"schema_version":"1","frame":"World","x":{"value":0,"unit":"m"},"y":{"value":0,"unit":"m"},"z":{"value":0,"unit":"m"}},"radius":{"value":0.001,"unit":"m"}},"size":{"value":0.0002,"unit":"m"}}
```

上の中心は一般的な構文例であり実モデルの座標ではない。中心はソースWorld座標、radius/sizeは正の長さ、sizeはglobal_size以下を要求する。既存 `BackendLocalRefinement` とnative背景場を再利用し、重なる球では小さい目標サイズを適用する。球はM/MMに依存せずSIで保存し、全値をrecipe digestへ含める。自動検出・自動細分化・fallbackではない。

静荷重専用の永続owner/run記録、既存Windows filesystem pin/publication lease、LocalBundleStore、RunnerAdapterを使用する。実行入力・mesh・profile・scope・owner generationを照合し、ローカルrunner発行snapshotだけを受理する。root exitだけでは公開しない。所有子孫のdrain、ログの唯一の `N O R M A L   T E R M I N A T I O N`、エラー終端不在、実XPLTの0..1秒の11要求状態、正確な最終時刻1秒と全有限fieldを要求する。クラッシュ後はstatusに中断診断を出し、PID採用や暗黙再起動を行わない。

出力対応表は認定済みnative XPLT reader identity/capabilityのみを再利用し、平面接触の物理的な認定を流用しない。dictionaryは `displacement`、`reaction forces`、`stress` のみ。既存の反力符号対応（raw -1、canonical +1）を適用した支持反力を、実際の外力との釣り合いで検査する。最終状態の力・変形後位置でのmoment・固定変位・有限displacement/stressをsummaryに記録する。力許容差は `max(1e-6 N, TOTAL力ノルム和×0.005)`、momentは `max(1e-9 Nm, TOTAL力ノルム和×CAD mesh bounding diameter×0.005)`、固定変位は `1e-12 m`。`maximum_von_mises_pa` は**要素平均Cauchy応力テンソルから算出したvon Mises値の最大**であり、`stress_basis=element_average_cauchy` を必須とする。積分点/局所の最大応力や降伏/安全性の合格ではない。

`sealed-results.xplt`、3つの `numeric-*.json`、`manifest.json`、`summary.json` とnative/solver実ログを保存する。statusは公開されたsealed出力・数値content digest・lineage・summary再計算を検証する。実行成功 `SUCCEEDED` と全体品質を分離し、CAD近似誤差・メッシュ依存性・局所最大応力の復元（`peak stress recovery`）・solver残差のscope認定・材料安全性は不足したまま `UNVERIFIED` とし、偽の総合PASSを生成しない。未測定のCAD近似を数値0のMeshQualityRecordとして記録せず、準備記録の `cad_approximation_status=UNVERIFIED` と未認定の理由で明示する。古い封印summaryへbasisを後付けせず、新しい要求を満たさない記録は完全性不合格のまま保持する。GUIはこの経路では起動しない。

構文の版固定一次資料：
[FEBio v4.12 FENodalForce](https://github.com/febiosoftware/FEBio/blob/v4.12/FEBioMech/FENodalForce.cpp)、
[ゼロ指定変位](https://github.com/febiosoftware/FEBio/blob/v4.12/FEBioMech/FEPrescribedDisplacement.h)、
[終端ログ](https://github.com/febiosoftware/FEBio/blob/v4.12/FEBioLib/FEBioModel.cpp#L1865)。
これらのsource根拠と実native実行証拠は別であり、本節は任意実CADでの成功を主張しない。

