# FEBio CAE Harness V2 — プロトタイプ実装・検証計画

文書版: 0.2 / 作成・更新日: 2026-09-07 / 状態: V2開発の開始基準。
ファイル名の日付は文書識別子であり、作成日ではない。
製品の振る舞いは[設計仕様書](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md)に従う。

## 1. 到達点と進め方

最初の到達点は、明示的な仕様からSTEP部品と新規剛体治具を用意し、接触押し込みを実FEBioで実行し、結果を読み取ってFEBio Studioで表示できることである。その経路へ自然言語入力、部分編集、再解析比較を接続する。

P0〜P1で実機適合性と共通契約を単一所有者が固定する。P2以降は固定した契約の下でinput/model、solver/FBS、autonomy、build/launchを別worktreeへ分離する。依存工程が合格するまでは後続の実行経路を完成扱いしない。

実装単位ごとに、収集できるテストの失敗をREDとして記録し、最小の実装でGREENにする。テストのみの変更と製品実装を小さな別コミットにし、受け渡しはレビュー済みのcleanなコミット列とする。収集失敗、実行環境の欠落、途中終了はRED/GREENの成立証拠にしない。

P0〜P6は合成形状を使う技術基盤の検証、P7は許可された実モデルによる運用受け入れである。プロトタイプ完成にはP0〜P7すべてが必要であり、最終BottomFrame実モデルE2Eを省略できない。ベイズ最適化は別のストレッチ工程とする。

## 2. 最初に固定するもの

| 決定 | 完了条件 |
|---|---|
| Python・配布 | Python 3.12、srcレイアウト、wheelから`febio-cae --version`が動く |
| ネイティブ対応表 | FEBio、Studio、Gmshの絶対パス・版・ハッシュと、実測した能力を記録 |
| 材料・要素・接触 | 仕様の候補ごとにネイティブ語彙、要素・面順、要求出力を実機確認 |
| XPLT読込 | 対応版、圧縮、必須の変数配置・符号・状態、独立照合方法を固定 |
| プレビュー | 起動方法、対象XPLT読込の確認方法、証拠の取り方を固定 |
| 共通型 | ケース版、質問、領域、mesh、bundle、run、result、quality、comparisonのschema v1 |
| 単位・予算 | 正規化と表示単位、実機の資源、数値profileと制限を定義 |

対応表は機械可読な製品設定へ実装し、実機の証拠を添付する。外部資料だけで能力を`verified`にしない。資料と実機が異なる場合は差分を記録し、対応宣言を限定する。実データの材料・負荷等の未知値を、この工程の既定値で埋めない。

## 3. フェーズと依存関係

| フェーズ | 依存 | 実装する範囲 | 終了条件 |
|---|---|---|---|
| P0: 骨格・互換性 | 本設計 | packaging、version、doctor、公式資料から独自に作る合成probe | clean wheelの起動、ネイティブ対応候補の実測記録。欠落能力は未検証と明示 |
| P1: 共通契約 | P0 | domain、ケース登録、質問世代、版、状態、原子的保存、単位 | 契約・不変条件・競合試験がGREEN、schema v1固定 |
| P2: CADとメッシュ | P1 | STEP調査、領域、剛体生成、メッシュ、品質、キャッシュ | 3種の治具、領域再対応、単位、要素と面順の検証 |
| P3: FEBio一貫実行 | P1、P2 | compiler、runner、reader、品質、preview | 合成接触ケースを実FEBioで解析し、数値照合とStudio読込確認 |
| P4: 自然言語操作 | P1、P3 | LLM adapter、意図・質問・型付き差分、CLI JSON | 根拠の保持、未確定条件での停止、古い回答拒否、実LLM接続経路 |
| P5: 編集・比較・復旧 | P2、P3、P4 | 部分変更、再実行、比較、予算付き再試行、クラッシュ復旧 | 実出力2件の比較、キャッシュ失効、割込み・改変時の正しい未完了判定 |
| P6: 配布・通し検証 | P0〜P5 | installed CLI全経路、データ境界、使用手順、性能記録 | clean wheelから合成形状の全経路と全ローカルゲートが合格 |
| P7: 実モデル受け入れ | P6、ケース使用許可 | 指定STEPの解析・編集・比較、最終BottomFrameケース | freshな実モデル結果、Studio表示、必須品質と比較の合格証拠 |

P0で新たに重大な互換性制限が分かった場合、2つの仕様文書と対応表を整合させてからP1へ進む。P0のprobeを製品経路の成功に数えず、製品アダプター完成後のP3・P6で改めて実行する。

環境が未成立でも、外部ツールに依存しない型・状態・単体テストの作業は進められる。ただしP0の必要な互換性確認を合格扱いせず、未確定のネイティブ能力をP1の固定契約へ対応済みとして埋め込まない。

### 3.1 各フェーズのRED/GREEN対象

次のコマンドは実装時に実行する計画であり、未実行である。各フェーズでテストを先に追加する。存在しないテストパスへの実行やimport errorをREDとして数えない。初期骨格は収集可能にし、期待する製品動作のassertion failureを記録する。

| フェーズ | 先に失敗させる代表動作 | REDコマンド | GREENコマンド |
|---|---|---|---|
| P0 | CLIの版表示と能力欠落の構造化診断 | `python -m pytest tests/unit/test_bootstrap.py --basetemp .local/verification/P0-red-01` | `python -m pytest tests/unit/test_bootstrap.py --basetemp .local/verification/P0-green-01` |
| P1 | 必須根拠なしの凍結、古い回答、同時更新を拒否 | `python -m pytest tests/unit/contracts --basetemp .local/verification/P1-red-01` | `python -m pytest tests/unit/contracts --basetemp .local/verification/P1-green-01` |
| P2 | 面の誤対応、単位競合、治具と部品の共有節点を拒否 | `python -m pytest tests/component/geometry --basetemp .local/verification/P2-red-01` | `python -m pytest tests/component/geometry --basetemp .local/verification/P2-green-01` |
| P3 | 別run・途中・書込中の出力を公開しない | `python -m pytest tests/component/febio --basetemp .local/verification/P3-red-01` | `python -m pytest tests/component/febio --basetemp .local/verification/P3-green-01` |
| P4 | 未根拠の条件と許可されないLLM操作を適用しない | `python -m pytest tests/component/autonomy --basetemp .local/verification/P4-red-01` | `python -m pytest tests/component/autonomy --basetemp .local/verification/P4-green-01` |
| P5 | stale patch、無効キャッシュ、異なる比較軸を拒否 | `python -m pytest tests/component/revisions --basetemp .local/verification/P5-red-01` | `python -m pytest tests/component/revisions --basetemp .local/verification/P5-green-01` |
| P6 | wheel外のソースを必要とするCLI配布を検出 | `python -m pytest tests/component/distribution --basetemp .local/verification/P6-red-01` | `python -m pytest tests/component/distribution --basetemp .local/verification/P6-green-01` |

再実行時は末尾の識別子を変更し、未使用のbasetempを使う。RED/GREENの件数・exit code・ログは実行結果から記録し、計画に仮の合格件数を書かない。P7は製品受け入れであり、意図的に実モデルを失敗させるREDを要求しない。P7で不具合が見つかった場合、実データをGitへ持ち込まず最小の合成回帰テストを先に作る。

## 4. 検証の種類と範囲

`python -m pytest`の標準testpathsは`tests/unit`と`tests/component`とし、外部の実モデルや認証情報を必要としないローカルゲートを構成する。`tests/native`と`tests/e2e`は明示的な別ゲートとして実行し、標準テスト合格から実ソルバー・Studio・LLM・実モデルの成功を推定しない。

実装時に収集範囲とテスト一覧を記録する。必要なnative/E2Eで実行ファイルや資格情報が不足する場合は環境未成立として報告し、skipによってゲートを合格扱いしない。

| Gate | 種類 | 確認する振る舞い |
|---|---|---|
| CT-01 | unit | 物理根拠、単位、schema、対応範囲、必須値、未指定とゼロ |
| CT-02 | unit/component | 草案世代、質問の一度だけの適用、親版一致、登録からの実行権限 |
| CT-03 | component | 原子的保存、二重起動防止、リンク・パス境界、失敗後の復旧 |
| GM-01 | component/native | STEP単位・ボディ選択と、3種類の剛体生成の寸法・姿勢 |
| GM-02 | component/native | 領域、節点順、面方向、Tet10、接触面の非共有、品質 |
| GM-03 | component/native | 再メッシュの領域継承、形状変更での再解決、キャッシュ改変検出 |
| FB-01 | component/native | 材料・剛体・支持・接触・運動・出力要求のFEBio変換 |
| FB-02 | component/native | 実行の所有、子孫の排出、停止、タイムアウト、残存プロセス |
| FB-03 | component/native | stale/truncated/改変出力の拒否、XPLT必須変数・状態、反力符号 |
| QA-01 | native | 合成の既知解・接触参照解・メッシュ依存性・釣り合い |
| VW-01 | native/manual | 公式Studioで対象XPLT、最終状態、変位等の読込・表示確認 |
| AI-01 | component | 自然言語からの提案、根拠不明時の質問、LLM出力のスキーマ・権限検証 |
| AI-02 | native | 実際のLLM接続から日本語意図→草案→質問→版確定までの経路 |
| RV-01 | component/native | 元版保存、型付き差分、再解析、変更依存性と予算 |
| CP-01 | component/native | 共通移動量、単位・領域・測度、意図した差分、ゼロ基準値、補間範囲 |
| PK-01 | local/installed | 全ローカルゲート、wheelの起動、インストール先からのimport |
| E2E-01 | installed/native | 合成STEP→実FEBio→検証→Studio→部分変更→比較 |
| E2E-02 | installed/real model | 許可された実STEPの同じ全経路 |
| E2E-03 | installed/real model | 最終BottomFrameケースの全経路と指定の品質・比較 |

ネイティブ試験はモデルの出所、実行backend、結果reader、viewerを別項目で記録する。合成モデルで実FEBioを実行した記録には両方を明示する。モックのログ・XPLT・画面による試験は合成証拠であり、公式アプリケーションの成功証拠に置換しない。

## 5. 数値検証ケース

以下はソフトウェアを検証するための合成条件であり、実部品の材料値や許容値ではない。許容値はこのプロトタイプの検証方針として設定するAssumptionである。解析結果は未取得であり、計算した参照値と実測結果を分けて記録する。

### 5.1 均一ひずみの弾性パッチ試験

線形弾性の直方体に、軸ひずみとPoisson収縮を含む既知の均一変位場を境界条件として与える。単軸応力状態と整合する境界を全体で定義し、底面全面固定をこの解析解の代用にしない。要素変換、単位、応力、反力符号の確認に使う。

参照式は`F = E A delta / L`。`E`はYoung率、`A`は初期断面積、`L`は初期長さ、`delta`は圧縮量の正の大きさである。

| 入力・参照 | 値 |
|---|---|
| 合成材料 | E = 1 MPa、nu = 0.3 |
| 形状・運動 | L = 10 mm、A = 100 mm²、delta = 0.01 mm |
| 参照力の計算 | 1 N/mm² × 100 mm² × 0.01 mm / 10 mm = 0.100 N |
| 適用条件 | 小ひずみ、等方線形弾性、均一場、接触なし |
| 合格基準 | 反力・代表応力の相対誤差1%以内、変位場と向きの一致 |

E、A、deltaの相対誤差は力へ一次に、Lの相対誤差は符号を反転して一次に効く。単位変換した等価入力でも同じSI結果になることを別試験で確認する。

### 5.2 剛体球によるHertz接触試験

摩擦のない剛体球と等方線形弾性半空間の小変形接触を参照とする。[MITの講義資料、式73](https://ocw.mit.edu/courses/20-310j-molecular-cellular-and-tissue-biomechanics-spring-2015/2910b668e59306bcf9ba5046b51215e5_MIT20_310JS15_Kamm2.2.pdf)に示された関係から、`F = (4/3) E* sqrt(R) delta^(3/2)`、`E* = E/(1-nu²)`とする。Rは球半径、deltaは接触開始からの押し込み深さである。

| 入力・参照 | 値 |
|---|---|
| 合成材料・接触 | E = 1 MPa、nu = 0.3、摩擦なし、接着なし、剛体球 |
| 形状・運動 | R = 10 mm、delta = 0.01 mm、初期接触、重力なし |
| 参照力の計算 | (4/3) × (1/0.91) N/mm² × sqrt(10 mm) × (0.01 mm)^(3/2) = 0.00463 N |
| 適用条件 | delta/R = 0.001、弾性、小さい接触領域、十分大きい試験片 |
| 合格基準 | 最終反力の参照誤差5%以内。接触形状・符号・対称性を確認 |

次元は`N/mm² × mm^(1/2) × mm^(3/2) = N`で整合する。力はEに一次、Rの平方根、deltaの3/2乗で依存し、押し込み深さの誤差の影響が大きい。初期隙間を治具移動量に混ぜない。

有限の試験片と離散的な球面を使うため、試験片寸法を拡大した場合の反力差1%以内、接触近傍を連続2回細分化した反力差がそれぞれ2%以内であることを、上記5%の判定と合わせて要求する。底面は十分遠方で固定し、その他の非接触面は自由とする。境界の影響、球面近似、penalty設定を独立に調べる。有限境界の未収束を参照解との不一致として隠さない。

### 5.3 実際の接触profileの回帰試験

Hertzは摩擦・大変形一般を保証しない。円柱・直方体、有限摩擦、対応するneo-Hookeanの各公開profileについて、小さな独自合成モデルで接触・剛体運動・収束・出力を検証する。非線形材料にはエネルギー密度の解析的微分等から得る単純変形の参照値も用意する。

摩擦ありの条件は、接触圧が正の区間で滑り・固着の成立とCoulomb限界を確認する。力の釣り合い、食い込み、メッシュ依存性の基準はprofileごとに事前登録する。既存出力をコピーしたgoldenファイルだけで正しさを主張しない。基準を定められないprofileは対応表で未検証とする。

## 6. 逆境・ライフサイクル試験

| 条件 | 期待する結果 |
|---|---|
| 材料や摩擦が不明 | 未確定フィールドを示し、凍結・実行をしない |
| 以前の質問に回答 | 現行草案を変更せず、世代不一致を返す |
| 版確定後にCAD・メッシュ・入力を差替え | ハッシュ・実体不一致で実行または公開を拒否 |
| 親プロセス終了後も子プロセスが出力を書込む | 排出・writer終了確認前に結果を公開しない |
| 有効な古いXPLTを新runの出力先に置く | 所属・生成経路不一致を検出する |
| reader実行中の切詰め、後日の出力改変 | 完全性不合格。以前の評価・preview確認を失効 |
| CLI停止後にresumeを二度要求 | 所有世代と保存状態を照合し、二重起動しない |
| ツール起動失敗、環境欠落、未知XPLT版 | 実行失敗・能力未対応として返し、物理質問にしない |
| 再メッシュで面番号が変わる | 名前・規則・形状版から再解決し、曖昧なら未解決 |
| 接触面が空、誤方向、節点共有 | 入力検証で拒否 |
| 比較の単位・ROI・応力測度が異なる | 整合変換が定義できなければ比較不可 |
| LLMが任意コード・XML・外部パス操作を提案 | 実行せず構造化診断を返す |
| 時間・試行・LLM予算を使い切る | 到達点と停止理由を保存し、無限再試行しない |

上記のファイル・プロセス試験は、専用の合成データと所有するプロセスだけで実施する。実ユーザーのFEBioやStudioセッション、実モデルを障害注入の対象にしない。

## 7. 必須ローカルゲートと配布確認

各実装フェーズの最終コミット候補に対して次を実行する。コマンド開始時のcwd、Python実体、対象SHA、dirty状態、開始・終了、exit code、ログを記録する。

```powershell
python -m pytest
python -m ruff format --check .
python -m ruff check .
python -m mypy src tests
python scripts/scan_cae_data.py --root .
python -m build
```

pytestには実行ごとの新しいbasetempを指定した収集・実行記録も残す。標準コマンドの実行でbasetempを設定する場合、設定・環境を含めた有効コマンドを記録する。追加・再実行した試験の件数を合算して、一回の全体試験件数にしない。

P0でCAE境界スキャナーを作成する。追跡ファイルとコミット候補を検査し、実CAEファイル・認証情報・デスクトップ状態・`02_CAE`を拒否する。`.local`等の生成・実行領域の除外を明示し、除外領域がGitで追跡されていないことも検査する。除外や拡張子判定だけで全データの安全性を保証したことにしない。

installed smokeは、当該buildのログで指定されたwheelを一意に選び、名前・SHA-256を記録して行う。最新時刻のwheelを推測して選ばない。次は予定する手順である。

1. 未使用の`.local/verification/<phase-run>/installed-env`へPython 3.12でvenvを作る。
2. venvのPythonの絶対パスで、当該wheelを通常インストールする。editable installは使わない。
3. 未使用の`.local/verification/<phase-run>/installed-cwd`へ移動し、PYTHONPATHの影響を除く。
4. venv内の`febio-cae.exe --version`を実行し、exit codeと版を記録する。
5. import元がvenv内のインストール先であることを確認する。
6. P6では同じ環境のCLIでE2E-01、P7ではE2E-02・E2E-03を実行する。

すべての実行記録には展開済みの絶対パスと引数を残す。wheelの起動だけを解析経路の成功に数えない。

## 8. native・実E2Eの実行契約

実装後の明示コマンドは次を基準とする。対応するケース・実行ファイル・許可領域は環境設定から型付き設定へ解決し、試験開始時に検証する。実ケースのパスや認証情報をGitへ書かない。

```powershell
python -m pytest tests/native -m "febio" --basetemp .local/verification/native-febio-01
python -m pytest tests/native -m "llm" --basetemp .local/verification/native-llm-01
python -m pytest tests/e2e/test_installed_synthetic.py --basetemp .local/verification/e2e-synthetic-01
python -m pytest tests/e2e/test_authorized_real_case.py --basetemp .local/verification/e2e-real-01
python -m pytest tests/e2e/test_bottomframe_final.py --basetemp .local/verification/e2e-bottomframe-01
```

P0でmarkerと収集対象を登録する。選択対象ゼロ、必要項目のskip、資格情報不足、未回収の実行はゲート未成立とする。Studioの人による読込確認はpytestとは別のVW-01記録とし、テストが画面表示を検証していないのに表示成功を主張しない。

E2E-02・E2E-03は、対象CAD・ケース領域・許可操作・物理条件・評価基準が明示された後に行う。明示済みの許可は引き継ぐ。実`02_CAE`への書込みはこの最終工程に限り、元データを保存した新規実行領域で実施する。

各実モデルE2Eでは、STEP調査→条件確定→版固定→剛体生成と接触解析→必須品質→Studio確認→1項目以上の変更→新規解析→同条件の比較までを記録する。BottomFrameの正式な入力や条件が未提供の場合はE2E-03を未実施とし、プロジェクト完成とは報告しない。

## 9. 要求と検証の対応

| 要求 | 必須Gate | 主工程 |
|---|---|---|
| REQ-01 | GM-01、FB-01、E2E-01、E2E-02、E2E-03 | P2、P3、P6、P7 |
| REQ-02 | PK-01、E2E-01 | P0、P6 |
| REQ-03 | CT-01、AI-01、E2E-02 | P1、P4、P7 |
| REQ-04 | GM-01、CT-01 | P2 |
| REQ-05 | GM-02、GM-03 | P2、P5 |
| REQ-06 | GM-02、QA-01 | P2、P3 |
| REQ-07 | FB-01、QA-01 | P3 |
| REQ-08 | CT-02、FB-01、FB-03 | P1、P3 |
| REQ-09 | CT-03、FB-02、RV-01 | P1、P3、P5 |
| REQ-10 | FB-02、FB-03 | P3、P5 |
| REQ-11 | QA-01、E2E-02、E2E-03 | P3、P7 |
| REQ-12 | VW-01、E2E-01、E2E-02、E2E-03 | P3、P6、P7 |
| REQ-13 | CT-02、RV-01 | P1、P5 |
| REQ-14 | CP-01、E2E-02、E2E-03 | P5、P7 |
| REQ-15 | AI-01、AI-02 | P4 |
| REQ-16 | CT-02、CT-03、AI-01 | P1、P4 |
| REQ-17 | CT-03、PK-01、E2E-02 | P1、P6、P7 |
| REQ-18 | FB-02、GM-03、RV-01、AI-01 | P3、P4、P5 |
| REQ-19 | PK-01、E2E-01 | P0、P6 |
| REQ-20 | PK-01、E2E-01、E2E-02、E2E-03 | P6、P7 |

## 10. 作業分担と受け渡し

開発先は`https://github.com/A6721jpn/cae-harness.git`の`V2`。このV2は新規設計文書から始まる独立履歴とし、旧CAE Harnessのコード・テスト・スキーマ・成果物・ケースを参照・移植しない。fetch対象はV2と明示的に作成したV2作業ブランチへ限定する。

| 役割 | モデル・設定 | 責務 |
|---|---|---|
| PM | `gpt-6-astra` / `medium`、独立会話タスク | 要求・依存・作業範囲・証拠・レビュー・V2統合を管理する |
| ワーカー | `gpt-5.6-luna` / `max`、Luna Spawn経由の独立会話タスク | 指定ファイルをテスト先行で実装し、cleanなコミットと検証記録を渡す |
| コードレビュー | `gpt-6-astra` / `medium`、独立会話タスク | baseとcandidateの正確なSHAを対象に読み取り専用で検査し、PMへ判定を返す |

P0〜P1の実装担当ワーカーは一人に限定し、共通契約の所有を分裂させない。PMは管理・統合を担当し、同じ製品ファイルをワーカーと同時編集しない。固定後に領域別ワーカーを追加する場合もLuna Spawnを使い、作成と続行の各要求でLuna/maxを明示する。モデル・effortは実行メタデータで確認し、不明なら未検証として報告する。

メッセージのモデル・effort指定は宛先のタスクに適用する。Luna/maxはワーカーの作成・続行だけに指定し、PMへの結果報告では宛先のAstra/medium設定を維持する。

ワーカーは結果をPMへ返し、PMが正確な候補SHAと検証記録をレビュー担当へ渡す。レビュー担当は修正を実装せず、重大度・再現条件・根拠・未検証ゲート・ACCEPT/REJECTを返す。Blocking/Highの未解消指摘がある候補は統合しない。PMは指摘を同じLunaワーカーへ差し戻し、修正後の新しいSHAで再レビューする。

V2への統合とpushはPMのみが行う。レビューされた候補に対して必要なローカルゲートを確認し、統合後に影響する検証を行ってから、非forceのpushでV2を更新する。worker/reviewerはremoteの削除、既存ブランチの変更、V2への直接pushをしない。タスクID・起動プロンプト・実行メタデータ・会話連絡簿はGit外のローカル調整領域で管理する。

P0〜P1の共通契約は単一所有者が管理する。固定後のworktreeは本リポジトリのレビュー済みbase SHAから作り、ブランチ名は`codex/`を使う。別リポジトリのコード・テスト・スキーマ・ブランチ・worktreeを参照・移植しない。

| 作業領域 | 主な許可ファイル | 共通契約への変更 |
|---|---|---|
| input/model | `src/febio_cae/adapters/geometry/`、`meshing/`、対応テスト | 個別に変更せず、所有者へ必要差分を提示 |
| solver/FBS | `src/febio_cae/adapters/febio/`、`viewer/`、対応テスト | 同上 |
| autonomy | `src/febio_cae/adapters/llm/`、対応application操作、対応テスト | 同上 |
| build/launch | `pyproject.toml`、`src/febio_cae/cli/`、`scripts/`、配布テスト | 同上 |

各作業依頼には、実際のbase commit SHA、目的、許可ファイル、禁止変更、入力契約、実際に実行するRED/GREENコマンド、完了基準、必須ゲート、未検証事項、cleanなコミット列による受け渡しを含める。上表だけを依頼文の代用にしない。統合者は差分をレビューし、統合後のSHAで影響ゲートを実行する。

最終許可工程前の実データ変更、許可済みremote以外への変更、共通型の無断分岐、要求にないGUI追加、別リポジトリからの移植は作業範囲に含めない。

## 11. フェーズ完了報告

報告先は`docs/reviews/`とし、実データや画面そのものはケース領域へ保存する。各報告は次を含む。

- 対象フェーズ、base・候補・統合後SHA、変更ファイル、dirty状態、`REMOTE_CONFIGURED`、V2のlocal/remote SHA。
- REDとGREENの展開済みコマンド、件数、exit code、実行ログ。
- 必須ローカルゲートの各結果、buildしたwheelの名前・ハッシュ、installed smoke。
- native・viewer・実モデルの個別結果と、モデル出所・backendの区別。
- 実行の入力bundle、結果manifest、確認時ハッシュ、数値許容差と実測値。
- 未検証・失敗・環境未成立・未実施、残る作業、次の具体的タスク。

中断や環境不成立の出力を製品不具合の確定証拠にも成功証拠にも使わない。文書のみの変更には製品テストのRED/GREENを捏造せず、文書のリンク・要求対応・内容整合の検査結果を記録する。

## 12. ストレッチ工程

P7後、ST-01の設計変数・CAD編集レシピ・目的・制約を具体化する。独立した条件・予算でベイズ最適化器を接続し、形状の成立、領域再対応、解析品質、観測の比較可能性、最終候補の新規解析を検証する。既存の解析契約と結果判定を再利用し、最適化専用の成功判定を重複実装しない。

## 13. 現在の状態と次タスク

この計画の作成時点では製品コード、製品テスト、実FEBio解析、Studio表示、実モデルE2Eは未実施である。P0〜P7は未着手で、仮の合格件数や性能値はない。

次タスクはP0である。まずPython 3.12の収集可能な最小骨格、version/doctorの失敗テスト、CAE境界スキャナーを作り、RED→GREENとwheel smokeを記録する。続いて登録されたFEBio・Studio・Gmshを合成probeで確認し、対応表と未検証項目を固定する。
