# 個別承認の単一ソリッド静的辺荷重 — 実行・変更記録

## 対象と境界

基線commitは `950257129a05d72ccec2e7a3a58a2f81ed6b8b74`、候補は本記録を含む `orca/acceptance-integration` の完了報告SHAを正とする。V2への統合・push・他PCでの確認は実施しない。既存MVPの8手順、接触 `CaseSpec`、M2の合成証拠、P7/BottomFrameの完了条件は変更しない。

ユーザーが指定した実STEPと最新ASSUMPTION 1〜4だけを対象にする。前後の指定曲線へWorld−Zの各**合計**10 N、2本合計20 N。青の中央下面A面だけをXYZ完全固定し、B/Cと赤いL形ROIを追加拘束にしない。登録済みA5052の弾性値を維持し、新しい材料・重力・接触・治具を作らない。過去の単一面荷重、密度荷重、固定取消しの指示や助言は今回の物理条件へ流用しない。

実CAD、実際の選択ID・座標・CADハッシュ、数値結果、ログ、画像、絶対パスはGitとダッシュボードへ載せない。以下の `<PRIVATE>` はGit外の承認済みケース証拠root、`<WORKTREE>` は候補作業ツリー、`<NATIVE_PYTHON>` は既存native環境、`<FEBIO>` は確認済み実solverを指す記号である。

## 実装の変更ファイル

- `src/febio_cae/domain/static_load.py`：独立した型付き要求、各曲線TOTAL力、line3の3点Gauss積分と共有節点合算、単位・物理領域の凍結。
- `src/febio_cae/adapters/geometry/static_load.py`：認証済みGmsh/OCC、単一原形STEP、明示native単位・アルゴリズム・局所球・幾何政策、完全外表面・CAD選択・正のTet10写像。大規模メッシュの不要な投影/辞書/hex payloadを解放する。
- `src/febio_cae/adapters/febio/static_load.py`：接触/剛体なしの入力、各軸ゼロ指定変位、節点に分配した合計力、3つの場出力。
- `src/febio_cae/storage/static_load.py`：不変準備と単一solver試行、実owner/run/attempt、保持したcleanup権限、drain後の保存失敗再送出。
- `src/febio_cae/application/static_load.py`：公開prepare/run/status、実RunnerAdapterとXPLT読取、完全性・数値照合、封印出力と再計算されたstatus。
- `src/febio_cae/cli/static_load.py`、`src/febio_cae/cli/main.py`：公開CLIと既存終了コード規則。既存接触CLIは維持する。
- `tests/unit/contracts/test_static_load.py`：合計力/共有節点/符号、角・中間節点の支持重複、密度/意味不正、巨大JSON整数、ソース/選択束縛、局所球、中断状態の8試験。実モデルのIDを用いない。
- `tests/component/geometry/test_static_native_errors.py`、`tests/component/cli/test_static_load_errors.py`：入力/環境/完全性の分類、元の理由、live leaseと再利用の非破壊競合、存在しないroot、壊れた公開記録、平均応力の意味と未検証品質を合成データで確認する。
- 設計仕様書の独立操作契約、実装ノート§12、計画書の個別承認現在地、レビュー索引と本記録。

## 追加レビュー前のネイティブ実行と失敗の保存

実CADの準備要求は17回開始・17回終了し、16/17回目が `PREPARED`。それ以前の失敗、時間切れ、ネイティブaccess violation、診断補助の失敗、PLC交差、メッシュ封印時のメモリー不足をGit外に保持する。成功2回のrequest/mesh digestは同一だった。原STEPの終了時SHA-256は開始時と一致し、全ソースCAD面、選択固定面、両荷重曲線の対応と全Tet10の正値・外表面完全被覆を確認した。

曲面投影でのnative破損を隠さず、標準 `Mesh.SecondOrderLinear=1` を型付き要求で明示した。変位補間は二次Tet10のまま、幾何はアフィン近似である。CADの修復、面削除、形状変更、自動fallback、上限拡張はしていない。CAD近似の妥当性は独立した `UNVERIFIED`。

実FEBio 4.12の起動は**2回**、Studioは**0回**。初回は終端と11保存状態・有限場・固定変位を確認したが、`zero displacement` からXPLT支持反力が記録されず、力/モーメント照合が不合格となりexit 6／`FAILED`で封印結果を保持した。

既存の認定compilerと同じXYZの `prescribed displacement`／値0／relative 0へそろえた。固定面・節点・材料・荷重・時間・許容差は変えていない。新たなrootを実prepareし、2回目は195.51秒／exit 0／`SUCCEEDED`。実出力の有限変位/応力、固定変位、外力と支持反力、変形後位置でのモーメントの4照合がすべて `PASS`。正常終端・0..1秒の11状態・実XPLTと3つの数値場を封印しmanifestを保存した。公開statusもexit 0でowner/process/profile/lineage/出力digestと数値summary再計算を受理した。

別の使い捨て合成STEPで最終geometryコードを公開prepareから1回実行し、6ソース面保持、正のTet10・完全外表面、局所球、アフィン幾何、指定TOTAL力の保存を確認した（exit 0／118.73秒）。これは実CADの成功証拠と混同せず、実FEBioを追加起動していない。

## コマンドと検証

nativeのsource呼出しは `<NATIVE_PYTHON> -I -B -c "import sys;sys.path.insert(0,'<WORKTREE>/src');from febio_cae.cli.main import main;raise SystemExit(main())"` を用いた。以下は追加Opusレビュー前の候補 `518317c` の履歴であり、追加修正後の合格とは区別する。実パスを記号化した実argvと結果は次のとおり。

| コマンド | 結果 |
|---|---|
| `static-load prepare --root <PRIVATE>/case-02 --cad <ORIGINAL_STEP> --request <REQUEST> --json` | exit 0、164.51秒、PREPARED |
| `static-load run --root <PRIVATE>/case-02 --solver <FEBIO> --json` | exit 6、190.16秒、境界数値照合FAILを保持 |
| `static-load prepare --root <PRIVATE>/case-03 --cad <ORIGINAL_STEP> --request <REQUEST> --json` | exit 0、148.67秒、PREPARED、同じmesh/request |
| `static-load run --root <PRIVATE>/case-03 --solver <FEBIO> --json` | exit 0、195.51秒、SUCCEEDED、4照合PASS、全体品質UNVERIFIED |
| `static-load status --root <PRIVATE>/case-03 --json` | exit 0、32.18秒、実際の公開記録と完全性を受理 |
| `python -m pytest tests/unit/contracts/test_static_load.py tests/component/cli tests/component/febio tests/component/geometry` | exit 0、522 passed、89.91秒 |
| `python -m pytest tests/unit/contracts/test_static_load.py` | 最終型注釈変更後exit 0、8 passed、0.18秒 |
| `python -m ruff check .` | 最終exit 0、指摘0 |
| `python -m mypy src tests` | 最終exit 0、224 source/test files、指摘0 |
| `python -m build --outdir .local/build-static-edge` | exit 0、sdistと通常wheel、9.96秒 |
| `python -m build --outdir .local/build-static-edge-final` | 最終CLI整形後exit 0、sdistと通常wheel、9.36秒。先のbuild証拠を上書きしない |
| `python -m pytest`（初回コマンド期限600秒） | TIMEOUT／非PASS、1809件収集、63%時点で中断。テスト失敗とは判定しない |
| `python -m pytest`（最終・無絞込み、期限3600秒） | exit 0、1809 passed／0 failed、3510.04秒。初回TIMEOUTを別記録として保持 |
| `python -m ruff format --check src tests scripts` | exit 0、226 files already formatted |
| `python scripts/scan_cae_data.py --root .` | 候補13ファイルのstage後exit 0、checked/tracked/index各292、diagnostics/issues各0 |
| `<FRESH_PYTHON> -m pip install --no-deps <WHEEL>` | exit 0、新規環境へ通常インストール |
| `<FRESH_PYTHON> -I -B -m febio_cae --version`／`static-load --help` | exit 0、0.1.0と3公開操作を観測 |
| `<FRESH_PYTHON> -I -B -m febio_cae static-load status --root <PRIVATE>/case-03 --json` | exit 0、43.17秒、ソース外cwdから実結果と不変系譜を受理、solver起動0 |

新規環境のstatic application/CLIがwheelのsite-packagesからimportされたことも `-I -B` で実測した。wheelは `febio_cae-0.1.0-py3-none-any.whl`、425572 bytes、SHA-256=`ce9ba881fce7d13f776cecfa7d3775099e2419496763bb0d20aa61c2cf71dfa2`。ソース呼出しの実求解と、通常wheelの結果読取の証拠を分離し、インストール済みwheelによる新しい求解実績を捏造しない。

最終CLI整形後の別buildで作成した候補wheelは同名、425580 bytes、SHA-256=`9e32290b0d71e9fb259570a2967d2c9a50495811dc82dd6392a75280ed7338bc`。既存の新規環境へ `pip install --no-deps --force-reinstall <FINAL_WHEEL>` で通常導入しexit 0／2.11秒を確認した。先のwheelとその実状態読取の証拠は別に保持する。
最終候補wheelからソース外cwd・`-I -B` で同じ公開 `static-load status` を実行し、exit 0／46.06秒で実封印結果・系譜・数値再計算を受理した。求解の再起動はしていない。

`python -m ruff format --check .` は初回exit 1でCLI登録部と過去のM2レビューMarkdown内Pythonコード片の書式を指摘した。今回変更したCLIは整形し、全コード226ファイルの書式検査を通した。`docs/reviews/2026-09-23-product-m2-preview-preparation.md` の過去記録は今回と無関係のためそのまま保持し、全体Markdownを含む書式ゲートの合格を主張しない。

初期ruffの7指摘とmypyの10型エラーは修正後の検査合格へ更新した。調査用native logger/export補助処理は製品経路から削除した。失敗履歴を後の合格で置き換えない。

## 追加レビュー後の検証履歴

5点の修正を統合し、次の検証を追加した。合成データ・保存済み実記録の読み取り・新たな実native求解を別の証拠として管理する。

| コマンド／確認 | 結果 |
|---|---|
| `python -m pytest tests/component/geometry/test_static_native_errors.py tests/component/cli/test_static_load_errors.py tests/unit/contracts/test_static_load.py` | 修正後exit 0、36 passed、7.09秒 |
| `python -m ruff check .`／`python -m mypy src tests` | exit 0、指摘0、226 source/test files |
| `python -m ruff format --check src tests scripts` | exit 0、228 files already formatted |
| 旧wheel／新版sourceの不在CAD面 `static-load prepare --json`、同一合成STEPと明示600秒要求、別root | 旧版exit 4／106.95秒、子ログは不在面を示すが公開JSONは元理由なし。新版exit 2／92.88秒、不在面の理由を公開JSONで保持 |
| 新版sourceの支持と荷重曲線の重複 `static-load prepare --json`、合成STEP | exit 2／98.61秒、中間節点を含む重複の理由を公開JSONで保持 |
| 不在rootの `static-load status --json`、旧wheel／新版source | 旧版exit 4でrootを作成。新版exit 2でroot不作成を実測 |
| 所有Windows Jobからnative Pythonだけを起動する使い捨て診断 | exit 0／0.44秒、子の実行と終了・drainを確認。Gmsh/FEBio起動0 |
| `python -m build --outdir .local/build-static-edge-reviewed` | exit 0／10.26秒、sdistと通常wheel |
| `python -m venv .local/wheel-static-reviewed-env` と `<REVIEWED_PYTHON> -m pip install --no-deps <REVIEWED_WHEEL>` | exit 0／8.59秒、同じPCの新規環境へ通常導入 |
| `<REVIEWED_PYTHON> -I -B -m febio_cae --version`／`static-load --help` | exit 0／0.58秒、ソース外cwdで公開CLIを確認 |
| installed wheelによる過去case-03の `static-load status --json` と前後の封印ファイルhash比較 | 公開statusはbasis欠落の完全性exit 6。確認scriptはexit 0／45.40秒、summary/manifest/XPLT/logのhash不変、solver起動0 |
| 修正後の `static-load prepare --root <PRIVATE>/case-04 --cad <ORIGINAL_STEP> --request <REQUEST> --json` | exit 0／160.51秒／PREPARED。実CAD準備は18回開始・18回終了、原STEPは不変、要求とnodes/elements/faces/setsは先の実成功と同一 |
| 修正後の `static-load run --root <PRIVATE>/case-04 --solver <FEBIO> --json` | exit 0／250.52秒／SUCCEEDED、4数値照合PASS、要素平均Cauchy応力のbasisを保存、局所最大応力の復元を含む全体品質UNVERIFIED。実FEBio起動計3回、Studio0回 |
| 修正後sourceの `static-load status --root <PRIVATE>/case-04 --json` | exit 0／47.91秒、owner/profile/lineage/封印hash/数値再計算を受理、solver再起動0 |
| 修正後installed wheelの `static-load status --root <PRIVATE>/case-04 --json` | exit 0／48.04秒、ソース外cwd・隔離モードで新しい実封印結果の系譜と数値再計算を受理、solver起動0 |
| 新規環境の `febio-cae --version` | exit 0／0.37秒、ソース外cwdで通常console entrypointを起動 |
| `python scripts/scan_cae_data.py --root .`（追加修正9ファイルのstage後） | exit 0／16.96秒、checked/tracked/index各294、diagnostics/issues各0 |
| `python -m pytest`（追加レビュー修正後、無絞込み・人工的なコマンド期限なし） | exit 0、1837 passed／0 failed、3424.37秒（wall 3425.00秒）。先の1809件の合格とは別に保持 |

最初の関連試験は35 passed／1 failed。合成fixtureがfloat型の時間上限を整数で直接組み立て、JSON読込後のrequest digestと一致しなかった。fixtureを正しいfloat値にそろえ、数値配列の型注釈とlint2件を修正して上記36件・静的ゲートを通した。製品のlineage照合を弱めていない。

不在面診断の先行120秒要求は旧版／新版で各1回のowned-process deadline（exit 4）となり保持する。これは入力不正分類の再現成功ではない。別の明示合成要求を既存の600秒上限内で実行して、同じ入力の失敗前・修正後を確認した。実CADの物理条件・上限は変えていない。旧wheelのZIP直接importはnative起動前に失敗したため、展開した同じwheelの未変更コードで比較した。

修正後wheelは427435 bytes、SHA-256=`78344f56e45beed6f20e0b913259e806bbdf3c9980ff453e6a25517acaf9de20`。通常導入したapplication/CLIがsite-packagesから読まれることを `-I -B` で実測した。新しいsummary契約は平均応力のbasisを必須とし、過去の封印summaryへ値を後付けしない。最初の使い捨てwheel確認は不要なエラー文言assertで停止したため、そのassertを削除して上記ファイル不変確認を完遂した。

## 助言と内部表示

Claude Code CLIは `--model claude-opus-5-5 --effort xhigh --permission-mode plan` と読取ツールで文書・コードを相談した。初期の成功2件では独立型付き静荷重経路、既存CaseSpec維持、実native mesh/RunnerAdapter/XPLT再利用、座標単位・局所球の有界実験を助言として取得した。追加相談のAPI 429を保持し、枠復旧後は同じ指定モデルで3件目の相談を取得した（exit 0／490.43秒／応答モデル `claude-opus-5-5`）。backendのeffortは独立には報告されていない。実CAD・結果・私有ログを相談先へ渡さず、今回のコード・一般試験・公開仕様だけを読ませた。

3件目のsource reviewは物理計算の具体的欠陥を示さず、native入力不正の終了分類、run競合の優先順位、平均応力の表示根拠、未測定CAD近似の偽の数値0、statusのroot作成/破損JSON境界の5点を指摘した。コードを修正し、未測定CAD近似と局所最大応力の復元は `UNVERIFIED` として記録する。source reviewを実求解・任意CADの認定・安全性の合格証拠にはしない。代替モデルは使わず、旧荷重助言は最新ユーザーASSUMPTIONへ明示的に従属させた。

内部ダッシュボードは状態/実行回数/未検証項目のみを更新した。実Chromiumで表示とスクリーンショットを観測し、リンク0、実パス/実CAD/数値結果なしを確認した。外部公開・新規公開URLは0。

## 未検証と次の判断

今回の単一ソリッド静解析は求解・封印・4数値照合まで実証した。ただしCAD近似、メッシュ依存性、局所最大応力の復元、solver残差の適用scope、材料降伏/安全性は `UNVERIFIED`。von Misesの報告値は要素平均Cauchy応力テンソルから算出した値の最大であり、局所ピークの最大応力とは主張しない。結果の大小だけで設計安全性を合格としない。Studio確認、実物照合、M3の全条件、P7の必須全E2E、最終BottomFrame、他PC再現は今回の成功に含めない。製品の `COMPLETE` または最終完成を宣言しない。

次に結果を設計判定へ用いる場合は、独立したメッシュ細分化/CAD近似の受入根拠と材料許容値・評価領域が必要。今回の採用済み物理条件について追加質問はない。別PC確認はユーザー指定どおり対象外。V2統合/pushは別途明示指示が必要である。
