# FEBio CAE Harness V2 — プロトタイプ実装・検証計画

文書版: 0.3 / 作成日: 2026-09-07 / 更新日: 2026-09-09 / 状態: V2開発中の工程・受け入れ基準。
ファイル名の日付は文書識別子であり、作成日ではない。
製品の振る舞いは[設計仕様書](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md)に従う。

## 1. 到達点と進め方

最初の到達点は、明示的な仕様からSTEP部品と新規剛体治具を用意し、接触押し込みを実FEBioで実行し、結果を読み取ってFEBio Studioで表示できることである。その経路へ自然言語入力、部分編集、再解析比較を接続する。

PM兼PdM（Astra X-high）が優先順位・範囲・技術方針・受け入れ基準・有限予算を決定し、単独の実装担当（Astra Low）が順次実装し、別タスクのレビュー担当（Astra Medium）が検査する。P0〜P1の共通契約は単一所有者が管理し、固定した境界からinput/model、solver/FBS、autonomy、build/launchを順番に接続する。モジュールの分離を理由に実装担当を増やさない。依存工程が合格するまでは後続経路を完成扱いしない。

実装単位ごとに、収集できるテストの失敗をREDとして記録し、最小の実装でGREENにする。テストのみの変更と製品実装を小さな別コミットにし、受け渡しはレビュー済みのcleanなコミット列とする。収集失敗、実行環境の欠落、途中終了はRED/GREENの成立証拠にしない。

テストは最低限とし、初期計画にない機能・試験の追加を抑え、計画した利用者経路を最短で完成させる。必要なRED/GREENと最終候補の必須ゲートを維持し、同じ候補への重複実行や類似条件の試験行列を増やさない。文書のみの変更ではリンク・差分・内容整合と独立レビューに限定する。

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

### 3.2 製品経路をつなぐ成果単位

進捗は、利用者が最後まで実行できる操作と残る依存で判定する。共通型の追加数や過去観測の資料修正を、製品経路の完成と置き換えない。最初の明示仕様経路はLLM接続を前提とせず、`case spec`から同じ登録・検証・凍結サービスを使う。

直近は設計11節の単一ユーザー信頼境界で、小さい合成STEPと明示条件による登録→剛体生成・mesh→`.feb`→実FEBio→XPLT検証→Studio表示を優先する。新しいnative試験は入力・期待値・有限予算を事前に固定する。追加要件はこの経路で再現した不具合と元仕様に必須のものに絞り、将来用途や想定上の攻撃への拡張は別バックログとする。既存の有効テスト、TDD、独立レビュー、統合ゲートと最終実E2Eを維持し、対象外の攻撃観測を合格試験へ変更しない。

| 成果単位 | 利用者の操作と合格条件 | 依存・所有 |
|---|---|---|
| 登録と仕様固定 | ケース登録→型付き明示仕様の投入→validate→freezeが動き、無根拠・古い世代・親不一致・重複回答を拒否 | 単一の共通契約所有者がdomain、storage、applicationを接続し、CT-01/02/03と必須ローカルゲートを通す |
| STEPから実行入力 | installed CLIからSTEP調査、領域解決、独立した剛体・mesh生成、検証済み`.feb`と固定bundle生成までつながる | 必要な契約境界のレビュー済みSHAから単独担当がinput/modelとsolverを順次実装する。未固定契約は分岐させない |
| 最小の実接触経路 | 事前登録した単一合成profileで実FEBio→同一試行のXPLT・必須数値検証→Studio読込確認まで完走する | 登録・所有・公開の経路と新規nativeの具体的な仕様・予算が成立してから実行する |

P1の残件は、草案・版、質問・型付き差分、mesh/bundle/attempt/result/quality/comparison/previewの共通schemaとcodec・状態遷移、登録した根拠・親・世代の検証、CAS・質問の一度だけの適用・凍結、原子的保存・path/link境界・復旧、およびCT-01/02/03で閉じる。これらを契約境界固定と登録/lifecycle接続のまとまりでレビューし、個々の値型だけの作業票を増やすことを目的にしない。境界を先に固定しても、この全体が合格するまではP1終了・schema全体固定とは報告しない。

過去のP0観測が必要な証拠を欠く場合、原本と不足を保持し、未受理のまま調査を閉じられる。一般化した検証器や過去台帳の修復は、それ自体を製品実装の前提にしない。必要な能力は、製品adapterと新しい事前仕様・予算を持つ合成試験で検証する。旧試行の再開や予算の暗黙リセット、未承認native実行、許容値の事後変更は行わない。

上記の最小経路はP2/P3の部分到達点であり、全治具・材料・摩擦profile、Hertz、編集・比較、P6/P7の代用ではない。最終の必須ゲートとBottomFrame E2Eは変更しない。

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
| PM兼PdM | `gpt-6-astra` / `xhigh`、独立会話タスク | 製品の優先順位・範囲・技術方針・受け入れ基準・有限予算を決定し、依存・証拠・レビュー・V2統合を管理する |
| 単独実装担当 | `gpt-6-astra` / `low`、既存の独立会話タスク1件 | 指定ファイルを最小限のテスト先行で順次実装し、cleanなコミットと検証記録を渡す |
| コードレビュー | `gpt-6-astra` / `medium`、独立会話タスク | baseとcandidateの正確なSHAを対象に読み取り専用で検査し、PMへ判定を返す |

全工程で実装担当は一人とし、複数の実装タスクへの並行発注を行わない。既存の他担当の成果・未完了変更は保存し、新たな実装は割り当てない。共通契約を分裂させず、レビュー済み境界に従って同じ担当がadapterを順次実装する。PMは正確なbase SHA、許可ファイル、依存と完了条件を作業票に記載する。PMは管理文書と統合を担当し、実装担当の製品ファイルを同時編集しない。作業の続行にはCodexのタスク機能を使い、Luna Spawnは使用しない。モデル・effortは実行メタデータで確認し、不明なら未検証として報告する。

PM兼PdMはユーザーの目的と承認済み範囲内で開発上の意思決定を行い、通常の技術判断を都度ユーザーへ差し戻さない。重要な判断の理由・影響・受け入れ条件を記録し、製品動作や受け入れを変える場合は実装前に2つの仕様文書を整合させる。根拠のない材料・荷重・拘束・接触・ROIの決定、未承認のnative・実データ操作、必須実E2Eの免除はこの裁量に含めない。

メッセージのモデル・effort指定は宛先に適用する。作業指示はPM兼PdMへAstra/xhigh、実装担当へAstra/low、レビュー担当へAstra/mediumを明示する。結果報告は宛先の設定を維持する。完了・失敗・判断依頼に応じて連絡し、重複ACKや変化のない待機ポーリングを行わない。

実装担当は結果をPMへ返し、PMが正確な候補SHAと検証記録をレビュー担当へ渡す。レビュー担当は修正を実装せず、重大度・再現条件・根拠・未検証ゲート・ACCEPT/REJECTを返す。Blocking/Highの未解消指摘がある候補は統合しない。PMは指摘を同じAstra Low実装担当へ差し戻し、修正後の新しいSHAで再レビューする。管理文書の指摘はPMが修正して再レビューする。

V2への統合とpushはPMのみが行う。レビューされた候補に対して必要なローカルゲートを確認し、統合後に影響する検証を行ってから、非forceのpushでV2を更新する。worker/reviewerはremoteの削除、既存ブランチの変更、V2への直接pushをしない。タスクID・起動プロンプト・実行メタデータ・会話連絡簿はGit外のローカル調整領域で管理する。

共通契約は単一所有者が管理する。既存の実装・レビュー用worktreeを継続利用し、レビュー済みbase SHAを固定する。必要な作業ブランチ名は`codex/`を使う。別リポジトリのコード・テスト・スキーマ・ブランチ・worktreeを参照・移植しない。以下は単独担当が順番に扱う変更範囲であり、担当者を増やす表ではない。

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

2026-09-09時点では、登録・明示仕様・検証・固定と限定的な平面プロトタイプ経路を実装している。固定した合成ケースで実FEBio・Studioを使った検証があるが、任意STEPの公開CLI経路、一般的なnative適合性、FB-03の独立したformat 3検証、実LLM経路と最終BottomFrame実モデルE2Eは完了していない。過去の件数・SHA・未検証項目は[検証記録](../reviews/README.md)に保持する。

次の開発単位は公開STEP準備経路である。PM兼PdMが既存成果と依存を確認して有限の作業票を固定し、単独のAstra Low担当へ渡す。新規native実行は対象入力・条件・予算・許可が揃ってから行う。体制改訂そのものを製品経路やP0〜P7の完了証拠にはしない。

最初の小区分であるGmsh adapterの準備前検査は54dc2c0927f0953925205f06262d4b27d48916b7で独立レビューACCEPT後、V2へ統合・push済みとなった。任意設定`expected_occt_version`、`require_step_ap214`、`cpu_workers`により、AP214のHEADER宣言、所有セッションのOCCT版、CPU数指定を検査し、既存の未指定経路とinspection/mesh契約を保持する。合成8件、全体1466件、必須ローカルゲートとfresh wheelの起動・import確認は合格し、統合先でも準備検査8件とCAE境界216ファイルの検査が合格した。ユーザーのAGENTS.md変更は保持した。復旧と再実行を含む証拠は[準備前検査の報告](../reviews/2026-09-09-step-preparation-admission.md)に保持する。次は有限プロセス予算を持つ公開`prepare-planar`と生成者が所有する生成・公開記録の結合であり、公開CLI・native・実E2Eの成功はまだ主張しない。

この小区分の受け入れは、設定APIの収集可能なRED、注入した合成Gmshで正常な検査・設定順序と不正な宣言・版証拠の拒否を確かめる最小限のGREEN、最終候補の必須ローカルゲート、fresh wheelのインストール・起動・import確認、正確なSHAのAstra Mediumレビューとする。追加のnative起動・インストール・ダウンロード・実モデル操作は行わない。検査対象はinspectとmeshの両入口とし、失敗時も所有セッションを解放する。次の小区分で同じAstra Low担当が有限予算付きの公開CLI準備と生成記録の結合を実装する。公開CLI・native・品質・実E2Eの未完了項目は継続する。

### 公開準備の実装単位

`case --state-dir STATE prepare-planar CASE_ID --file REQUEST --expected-generation N --json`を既存SpecUpdateRequestと現在の実backend処理へ接続する。物理条件を明示した平面・box・AsPlacedに限定し、要求からmesh・SUPPORTED・producer receiptを登録しない。geometryの生成digestとinspection digest、および対応部品選択のgeometry digestだけはprivate入力でnullを許し、今回の子inspectionで解決する。非null不一致は拒否し、共通domain schemaや物理的意味を変更しない。

Gmsh4.15.2/OCC7.8.1/AP214、wall600秒・生成1回・100000四面体/250000節点を既定とし、明示的な有限上限変更だけを許す。CPUは検出した利用可能数と明示上限、メモリは開始時利用可能物理量80%（総量以下）を用い、所有子プロセスで時間・メモリを強制して記録する。solver3600秒/1試行は後続工程の目標であり準備では起動しない。既存lease/CASとcase-local PREPARING/FAILED/PREPARED記録を使い、現input/spec/evidence/generation/backend/mesh/recipe/outputへ結び付ける。freeze後の失敗でもPREPAREDのない公開経路の実行・preflightを拒否し、既存demoは保持する。

2026-09-09 candidate-pair decision: the active public inspection/preparation baseline is Gmsh 4.15.2 / owned linked OCCT 7.8.1 / AP214, replacing the unsupported 8.0.1 expectation. The pinned official Windows wheel was measured in a separate identity-only owned session as `OCC version: 7.8.1`; the earlier pre-geometry producer failure remains failed. No OCCT-8-only requirement was found. This is an identity-grounded candidate correction only: retain strict exact/missing/ambiguous/wrong-version admission, unchanged geometry/units/tolerances and qualification gates. Future producer and reader require newly accepted artifacts, fresh evidence and separate release; identity alone does not close any geometry or E2E gate.

最小RED/GREENは公開操作、正常な隔離生成、必要な入力/版不一致、途中公開の拒否と所有プロセスのhard timeoutを検証する。最終候補の必須ゲート一式とfresh wheelの起動・隔離準備境界を実行し、正確なSHAでMediumレビューを受ける。今回の実行はsourceとnative-freeテストのみであり、実Gmsh/OCCT/FEBio/Studio・real LLM・実ケース操作を行わない。次は残る公開実行・preflightの接続とプロセス所有権を、既存の生成記録に基づいて進める。

公開準備はM1の世代・spec・evidence一致検査と公開CONFLICT/exit8の修正を含む9c7095c4899f6b83c4c68fa38ae73688160e3e5cで独立MediumレビューACCEPT後、V2へ統合・push済みとなった。最終候補の全体1474件と必須ゲート・fresh wheel確認、およびroot統合検査8件・CAE境界222ファイルが合格した。詳細と失敗履歴は[公開準備の報告](../reviews/2026-09-09-public-planar-preparation.md)を参照する。実native準備・一般的なsolver適合性・実E2Eの成功は意味しない。公開run-demoは既にPREPAREDから実行・preflightへ接続しており、同じ実行経路を重複追加しない。

次のsource単位はRV-01/CP-01に向けたPREPARED originのYoung率のみの子孫版再利用とする。既存case patch→validate/freeze→run-demo --preflightを使い、登録親の内容とhash・祖先を検証して元の不変PREPARED rootへ結び付ける。等方線形弾性のYoung率と必須根拠だけの変更を許し、mesh生成条件・profile・その他固定条件の変更は祖先receiptで承認しない。既存adoptionで子版結合を導出するが、新規mesh/PREPAREDとは主張せずroot receipt/meshを変更しない。現在草案との一致、M1とM1-CLI、失敗公開拒否を保持する。

この単位は、最小の公開子版回帰RED/GREENと安価な静的検査・CAE境界を通したcleanなコードを先に独立Mediumレビューへ渡す。修正が収束した後、同じ担当が必須全体テスト・ビルド・fresh installed境界を一度実行し、最終証拠と文書の適用範囲を確認してからPMが統合する。CODE_REVIEW_PENDING段階では全体テスト・ビルド・installed確認を必須のPENDINGとして明記し、免除・合格とは扱わない。実行許可はsourceとnative-free検査のみであり、native・LLM・実02_CAE・BottomFrame操作は含めない。

この単位の現在状態（2026-09-09追記）: `e63776ca384600c4e556690199c87ee56c3c4241` は独立MediumのCODE_ACCEPT済み。同じclean候補で全件1475件、format・lint・型検査・CAE境界223ファイル・build・fresh wheelインストールと公開子版preflight境界が新たに合格した。製品・テストはCODE_ACCEPTから不変であり、最終証拠・文書レビューとPM受け入れ・V2統合はPENDINGとする。正確なコマンド・wheel hash・分離範囲は[最終フェーズ記録](../reviews/2026-09-09-prepared-material-descendants.md)に記載する。必須実E2E・最終BottomFrameの未検証状態は変わらない。


Plan 13 status correction: prepared-material descendants were independently reviewed, accepted and pushed to V2 at `57caa690695959f805d31e17112bea623a20fa11`. The earlier pending integration statement is superseded. Next source task is the bounded P4 contract below; real E2Es remain unverified.


## P4 bounded source contract (2026-09-09 freeze)

The first public natural-language boundary is `case intent`, `case answer`, and
`case edit`. Each requires case ID, `--expected-generation`, `--operation-id`,
`--llm-settings`, and `--text`; answer additionally requires `--question`, edit
requires `--base`. Existing explicit `case spec` registers prerequisite typed
conditions and numerical policy; this slice adds no profile bootstrap framework.

Grounding accepts only whole affirmative field-labelled clauses, independently
matching normalized value, unit, entity and scope, or explicit adoption of an
already registered typed component with verified nested evidence and references.
Initial material fields are model, Young's modulus, Poisson ratio, strain
applicability and rate applicability. Japanese aliases are 材料モデル, ヤング率,
ポアソン比, ひずみ適用性 and 速度適用性. Unsupported prose, negation, hypotheses,
ambiguity and contradictions remain unresolved; quotes, number occurrence,
provider labels and model confidence are not physical authority. No inferred
physical defaults or generic confirmation of invented values are permitted.

Complete grounded components enter the existing draft. Incomplete physical
conditions produce ONE generation-bound grouped question, targeting whole
components when absent. Retained explicit facts are shown and not asked again;
raw statements remain immutable source evidence, never an alternate partial
material draft. Answers rederive bounded retained facts with new explicit facts,
consume the current question once, and issue a new group if still incomplete.
No full provider history is sent. Existing validate/freeze remains authoritative.
Initial edit supports only an explicit isotropic Young's modulus replacement,
bound before spending and publication to the exact current frozen parent, its
spec digest and current draft; application constructs CasePatch and verifies all
other physical/mesh-generating fields unchanged. No automatic freeze or execution.

Settings explicitly specify OpenAI Responses provider, model, key environment
variable NAME, Budget, input/output token limits and socket timeout. Missing
key/model/tool/profile is environment/unsupported (exit 4); numerical configuration
errors are input diagnostics (exit 2). Only missing required physics creates
NEEDS_INPUT/questions (exit 3). Public generation conflicts use exit 8. Narrow
existing validation classification corrections and pre-source lease checks are
permitted; frozen DTOs, codecs and validators are unchanged.

Budget remains per operation plus retries, not case lifetime. An explicit settings
Budget is a journaled bootstrap allocation before a draft Budget exists; thereafter
effective caps are minima, and explicit zero disables. One count request and at
most one generation use two durably debited request slots without retry, fallback
or re-entry resend. Admission reserves 2*I+O: I for counting is a LOCAL allowance,
not measured model usage or provider billing; I+O reserves generation. Release
only capacity justified by valid measured generation usage; uncertainty retains
capacity. This makes no financial guarantee. The operation journal records exact
ID/context/instruction/settings/request/schema identities, transitions, usage and
actual returned model. Changed payload with the same ID conflicts; identical
re-entry returns recorded outcome or uncertainty without sending/applying again.
Concurrent starts and crashed APPLYING publication remain conservative.

Reject known stale questions, parents and evidence before HTTP, recheck before
generation and under a short reentrant case lease before ANY draft/question/source
write. No case lease spans HTTP. Persist usage even when final CAS rejects.
Requests and responses are bounded to 256 KiB; children to 256 MiB and one CPU.
Elapsed/call/token/socket limits are explicit positive finite values (allocation
zero disables), and output limit is at least 16. Sequential count/generation
children reuse the unchanged geometry preparation `_run_owned` helper with ONE
enclosing deadline and remaining duration; late results are rejected, cleanup
ownership retained. Local termination does not establish remote cancellation.

Transport uses standard-library HTTPS to fixed api.openai.com/v1, no redirects
or transparent retries. Canonical common model/input/instructions/strict closed
schema fields are identical for count and generation; count includes schema and
an over-cap count prevents generation without truncation. Generation explicitly
sets store/background/stream false and disables tools; no history/conversation IDs.
Only expected reasoning metadata plus exactly one completed assistant proposal
text is accepted. Refusal/incomplete/malformed results cannot mutate specs; valid
known usage remains charged. Count receives no invented store parameter. No model
substitution. The key is resolved only in memory, never argv/files/logs/errors.
Source tests inject private transport/credentials without live secrets or network;
no public fake-provider option exists.

Official protocol references: [count input tokens](https://platform.openai.com/docs/api-reference/responses/input-tokens),
[create response](https://platform.openai.com/docs/api-reference/responses/create),
[structured outputs](https://platform.openai.com/docs/guides/structured-outputs).

P4 SOURCE CODE_READY requires the two focused autonomy test files, directly
affected question/CAS/CLI regressions, format/lint/types and CAE boundary. After
Medium CODE acceptance, full pytest/build/fresh wheel verification remains
REQUIRED and PENDING, followed by exact evidence review and PM integration.
Source tests do not establish AI-02, native/real E2Es or final BottomFrame success.


### P4 literal syntax and explicit settings

Each fact occupies a whole `field = value` (or `field: value`) line; Unicode NFKC
normalization is allowed. The entity is the current explicitly registered geometry
body and scope is this case; the provider must match both. For example, the
independently supplied clause `material.youngs_modulus = 1 MPa` is a quantity,
while `material.poisson_ratio = 0.3 1` supplies a dimensionless ratio. These are
syntax examples, not defaults or recommended physical values. Canonical model
values are `isotropic_linear_elastic` and `compressible_neo_hookean`; applicability
accepts only `applicable`, `applies`, or `適用可`. Model value aliases are
`等方線形弾性` and `圧縮性Neo-Hookean`.
More detailed unsupported prose stays unresolved. Japanese field aliases listed
above map to these same canonical material fields.

Component adoption is `support = adopt revision-id.support` (likewise another
physical component): the revision must already be registered in this same case,
with verified evidence and compatible explicit component references. Missing
components from that revision are not copied automatically. Do not mix whole
material adoption and material subfield assignments in one fact group.

The settings JSON keys are exactly `provider`, `model`, `key_env`, `budget`,
`input_tokens`, `output_tokens`, `socket_seconds`. `budget` uses the existing
schema-1 Budget projection (`max_elapsed`, `max_attempts`, `cpu_workers`,
`max_llm_calls`, `max_llm_tokens`). Model and key environment name have no defaults.
Retained source evidence is limited to sixteen sources and 64 KiB total text;
exceeding this bound diagnoses input instead of truncating or sending history.
Bootstrap allocation does not populate the CaseSpec Budget. Supply missing
numerical policy through explicit `case spec` before validation/freeze.


### Initial public native inspection (approved bounded source contract)

The public command is `case --state-dir STATE inspect CASE_ID --native
[--wall-seconds SECONDS] [--cpu-workers N] --json`; metadata-only inspect stays
unchanged. Native inspection works directly after create without GeometryIntent,
selected body, physical inputs, profiles or credentials. It reports observation,
not qualification, and does not assign body/support/contact/ROI meaning.

A thin service entry delegates to an application-local immutable InspectionPolicy.
Wall seconds must be finite positive, default/ceiling 600; CPU is an optional
positive integer not exceeding current availability. Boolean, non-finite, zero
or excessive policies fail as input (exit 2) before launching. No caller-supplied
module, executable, version, digest or backend report is accepted. One owned
child, zero mesh generations and no retries use the unchanged preparation
resource_snapshot/_run_owned primitives: available CPU/affinity and 80% available
physical memory (bounded by total). The enclosing operation uses one deadline,
including request, launch and response work, always passing remaining time.

Only the child loads plain GmshOCCBackend plus StepGeometryMeshAdapter, with
Gmsh 4.15.2, OCCT 7.8.1 and AP214 admission and measured module/hash/version
identity. Preparation's planar face override is not used. Before reading child
JSON, stat its private response and enforce max_response_bytes = min(16 MiB,
effective_memory_bytes // 16), independent of caller input. Strict shape/count/
finite checks and existing inspection_from_dict/adapter digest reconstruction
validate the complete response; oversized or invalid topology is rejected, never
truncated. Preserve observed bodies/faces/units/frame/SI geometry/defects and
source/inspection-scoped IDs, not permanent semantic IDs.

Resolve registered STEP and generation using a short existing evidence snapshot;
verify before launch, release all leases during the child, then reacquire a short
snapshot and recheck source bytes/digest and current draft generation before
returning. Concurrent generation changes produce CONFLICT 8, source corruption
integrity 6, missing/mismatched tools/AP214 or owned timeout environment 4.
Failures must not fall back to REGISTERED metadata success. No draft, intent,
generation, frozen/PREPARED or authoritative registry mutation occurs. Unique
case-local scratch holds transient operation/ownership evidence only; preparation
still performs its own current inspection.

Return schema-1 envelope (case_id, null revision_id/run_id, diagnostics,
next_actions), observed generation, existing domain geometry inspection,
validated backend topology, measured backend identity and effective limits.
INSPECTED means observation only; native_qualification remains UNVERIFIED and
physical decisions unresolved. Investigation figures remain separately pending.
No qualification/profile provisioning, catalog placeholder, new process framework,
wider placement, preview-open or general lifecycle/retry work is included.
Actual qualification authority is missing and is not a human physics question.
Python 3.12, CLI-only operation, no runtime Orca/Codex dependency and all existing
geometry/domain/codec/store/producer/ownership boundaries remain unchanged.

Source acceptance uses four collected public synthetic cases: initial inspection
without geometry/profile and unchanged draft/zero mesh; stale/tampered source or
report including generation change; unsupported child diagnosis including finite
policy/oversized response boundaries; and finite owned deadline/cleanup failure.
Record genuine RED then GREEN and cheap static/CAE gates before exact CODE review.
Full mandatory gates/build/fresh installed smoke follow CODE acceptance once,
then final evidence review and PM integration. Source evidence does not qualify
real Gmsh/OCCT/AP214, REQ-04/P2/P6 completion or any actual E2E.

P4 intent/answer/E-edit source is independently accepted and pushed on V2 at
`3f837e7337a1a7d089d2c2d71bdd8ea5ee595aa6`, including PREPARED E-only
descendants. Full 1483 tests and fresh installed synthetic evidence passed;
post-integration eight tests and CAE boundary passed. Live AI-02/API remains
pending, together with actual native/profile/FB-03/Studio/Computer Use and all
mandatory actual E2Es including final BottomFrame. Next is this bounded initial
inspection source slice, not profile bootstrap or project completion.

Measurement composition clarification: the private inspection adapter may use
a measurement-only GmshOCCBackend subclass calling unchanged
super()._prepare_owned_session, then record module path/hash/version/BuildInfo
from that same live owned session. Plain backend means unchanged body/face/
geometry algorithms and admission; no planar face override, second session or
probe, public backend API change, or frozen-file edit is permitted.

### P3 bounded signed-force arithmetic increment

Implement the optional `signed_force_sum` contract in design section 8.3 using
existing criterion/evaluation records: explicit sum aggregation, same scalar
component/frame and ordered full saved-state coverage, force units, canonical
signed values and disjoint (location, entity) contributions even through aliases.
Retain all existing data bindings. Evaluate the maximum absolute per-state signed
sum in N against exactly one explicit finite nonnegative force-valued max_value;
valid data yields PASS/FAIL and absent/incompatible/overlapping/nonfinite evidence
stays UNVERIFIED. No defaults, sign/axis corrections or changes to peak_abs_value.

Use focused synthetic RED/GREEN for opposite versus equal signs, intermediate
imbalance and invalid required evidence, followed by mandatory local gates. This
increment is scalar arithmetic only: force-system completeness, physical side and
applicability, mandatory public-quality coverage, solver residual validation and
native sign qualification remain pending. Studio correspondence alone is not
sign proof. Keep profiles unchanged; no REQ-11/P3/FB-03/E2E completion claim. Native
validation and the required real-model E2Es remain separate dependencies.

### P3 preview registered-quality consistency

Public preview completion uses the same registered-quality identity gate as
run-status: the exact recomputed assessment must match the registered assessment
for that result. Absence is effective quality UNVERIFIED/noncomplete with an
explanatory quality_reason; disagreement or corruption is an integrity error.
An exact match retains the recomputed status, including FAIL. Preview observation
and solver status remain independent and are preserved. Reading a summary never
registers missing quality. The embedded quality is the recomputed declared-criterion
assessment; quality_registration_status preserves that registered usability;
quality_status is extended by the mandatory coverage increment below.
Registered declared-criterion PASS alone is not evidence of complete mandatory
physical/numerical coverage; force-system completeness, applicability, native sign
and solver-residual obligations remain pending. This registration check changes no policy or persisted arithmetic schema.

Use focused synthetic registration tests before implementation: missing quality
registration cannot complete even with confirmed preview and successful solver;
exact registration restores the existing status and preserves FAIL; mismatched
or corrupt registration is rejected. Summary reads do not create quality assets.
This increment aligns the existing registration gate, not mandatory physical
coverage or native/E2E qualification. Keep those dependencies pending.

### P3 mandatory coverage/completion gate

Public post-run summaries independently enumerate execution/result completeness,
contact quality, motion/support/contact-set fidelity, quasi-static equilibrium,
solver residuals and mesh dependence, regardless of the declared criterion list.
Missing implementation or qualified evidence is UNVERIFIED with category-specific
reasons and prevents COMPLETE. The initial gate has no qualified PASS or
NOT_APPLICABLE producer; arithmetic methods, criterion names, arbitrary evidence
references and registered profile labels cannot discharge an obligation. Current
public paths therefore cannot attain mandatory numerical COMPLETE until qualified
verifiers exist. No physics, signs, thresholds or applicability exclusions are guessed.

Expose derived required_quality with the exact resolved revision/spec, mesh,
profile, attempt/bundle/manifest and arithmetic-assessment identities/digests.
Keep CriterionAssessment-shaped numerical rows separate from the physical
applicability/experimental-validation row, which does not participate in numerical
aggregation. These response projections do not modify or persist the arithmetic
QualityAssessment or its digest and accept no caller-provided qualification flags.

quality_status is the effective numerical result; quality_registration_status
preserves the registered-assessment usability and embedded quality stays unchanged.
A known FAIL in trusted recomputed arithmetic or required evidence takes precedence
over missing registration/unresolved coverage. Missing registration itself remains
UNVERIFIED in its separate field; corrupt or mismatched registration is INTEGRITY.
Solver success and confirmed preview remain independent facts. Successful runs with
unresolved mandatory numerical quality use task_status NEEDS_QUALITY; known quality
failure uses FAILED. NEEDS_PREVIEW is reserved for satisfied quality with required
preview still unconfirmed. No new exit code or stored run-state transition is added.
Missing capability/qualification does not itself create ASK_AND_BLOCK; genuinely
unresolved required case physics continues through existing grounded validation.
Physical corroboration alone is not a universal prerequisite for numerical completion.

Use focused bound synthetic public-summary RED/GREEN: registered peak/signed-sum
PASS and confirmed preview still need required numerical quality; omitted/renamed
criteria or reason-only exemptions cannot shrink the inventory; known FAIL persists;
run-status without a preview reports the same unresolved quality gap. Preserve
arithmetic registration/digests and independent physical applicability. Do not test
or claim a fictitious qualified PASS path. Native force-system/sign, residual and
mesh-dependence qualification and all required real E2Es remain pending. This gate
closes silent omission, not the missing verification capabilities or P3/REQ-11/FB-03.


### Owned solver-log binding (P3 bounded capture)

On the existing issued-owned, drained VALIDATING path, seal the optional fixed
output/solver.log alongside required output/results.xplt using existing FileEntry,
closed sealed_files and exact manifest membership. XPLT reader admission and owned
result sealing are bounded at 128 MiB, with 900,000 cumulative parsed blocks and
an unchanged 16 MiB per-block limit (including nested containers). These finite
prospective capacities are not native qualification; actual inventory and container
bytes require separate validation. The independent input/case.feb cap stays 32 MiB;
the opaque log has an independent 8 MiB resource cap and is never truncated.
Only genuine final-file absence under the verified owned parent is optional.
Present empty logs are retained; present invalid, oversized, unreadable or modified
logs are INTEGRITY failures, not missing evidence or numerical nonconvergence.
Validate all payloads before copying and register the closed set once. No late log
attachment, new persisted schema, compiler control or parsed convergence claim.

The public demo reader includes all sealed entries in its candidate manifest while
numeric observations remain XPLT-derived. Registered log tampering invalidates later
manifest/preview reads. An absent log permits otherwise valid XPLT publication but
supplies no residual evidence; mandatory quality remains UNVERIFIED/NEEDS_QUALITY.
Solver termination is independent. FAILED/CANCELLED runs receive no fabricated
manifest; the existing read-failure path may retain sealed attempt files without a
manifest. Capture of nonzero-exit/cancelled logs and observed native grammar,
residual qualification and required real E2Es remain pending.


### Public reported solver norms (bounded P3 printed-value policy)

Existing demo, run-status and preview required_quality responses expose a derived
reported_solver_norms report from the exact registered manifest/owned log/input,
solver executable/version, profile, revision and mesh context. Preserve raw block
text/order and original token byte spans, including nonfinal cycles, and select
only unambiguously accepted final nonlinear plus subsequent augmentation blocks.
Expected solved increments come from compiled input controls, not XPLT saved states.
Initially support only the observed FEBio4.12.0 solid/static ten fixed0.1 increments
ending at1, one sliding-elastic interface, explicit consistent enabled controls,
min_residual0 and known caps. Unknown/retried/adaptive/multiple-interface/cap or
inconsistent association remains UNVERIFIED. Observed max_ups reformation is not a
retry. Each nonlinear row retains INITIAL/CURRENT/REQUIRED; augmentation retains
CURRENT/REQUIRED. Never freeze a changing required value from an earlier row.

Compare exact finite nonnegative printed CURRENT against positive printed REQUIRED:
strictly below is reported-row PASS, strictly above FAIL, equality/zero/nonfinite,
disabled/shortcut/unsupported evidence UNVERIFIED. No epsilon, inferred norm units,
internal formula or full-precision convergence claim. Maximum gap remains the
literal native quantity, not averaged L2 or physical penetration. A trusted final
contrary row makes effective quality FAIL/task FAILED despite unrelated unknowns;
nonfinal/rejected or untrusted admission cannot manufacture finalFAIL. Report-level
PASS means only printed comparisons; mandatory solver_residual and
native_qualification remain UNVERIFIED. No automatic COMPLETE or capability-driven
ASK_AND_BLOCK. Existing arithmetic registration, integrity and physical display
remain separate; status exit0 continues to mean successful status retrieval.

Allow only optional contact minaug (integer>=0)/maxaug (integer>0, minaug<=maxaug),
optional boolean solver reform_augment, and optional positive integer max_ups under
fixed solver/qn_method type=BFGS. Preserve omission; add no defaults or profile flags.
No new persisted schema, process/runner behavior or log publication path. Fresh
bounded actual producer qualification and a formal completeness decision remain
required before mandatory residual PASS; synthetic software checks are not native
qualification. All other quality obligations and required real E2Es remain pending.

### Bounded norm-core extraction and manual-reader contract

Implement only the adapter-local assess_reported_norm_observations(source, log,
*, policy, profile, invocation) and its frozen SolverPolicy/motion-endpoint and
tool/argv/process input records. Share the complete existing bounded admission,
scan, final selection, strict Decimal comparison and aggregation with the public
wrapper; preserve public registered identity checks, error precedence, report
fields/digests, 8 MiB/ASCII, exact 4.12.0/input/echo/controls/argv/process agreement,
fixed ten increments and motion endpoints, grammar/caps and incomplete-prefix
versus contradiction behavior. Core bindings describe only supplied resolved
files, policy/motion, profile and invocation, never CaseRevision or OS ownership.
The local checker authenticates receipts separately; native qualification stays
UNVERIFIED. No reader/domain/schema/quality/controller/CLI change is included.

Restrict the standalone manual-benchmark reader identity exception to the local
validation harness: actual namespaced study ID as case_id, immutable pre-run
study-specification version as revision_id, SHA256 of exact canonical complete
study-specification bytes as spec_digest. The specification must bind accepted
manual provenance, actual prepared input/mesh/profile and frozen physical,
numerical/output policy. It is not CaseSpec/CaseRevision or STEP/inspection
provenance. Never substitute an arbitrary preparation-manifest hash or include a
future receipt in the pre-run specification digest. A local NumericalProfileRef
may reference the exact digest of a namespaced immutable accepted solver-scope
document record, without asserting public registration. The later actual receipt
separately binds specification, bundle, input, mesh, process/output and true
owner/drain lineage; the harness verifies these associations and labels local
records/results explicitly. Constructor acceptance does not establish ownership.
Existing reader equality/path/tool/thread/entity/state checks stay enabled. Local
records/reports never enter CaseStorage or the public compiler/controller/
QualityAdapter, whose production CaseRevision semantics stay unchanged. Local
arithmetic remains validation-owned under predeclared policy; public quality and
mandatory/native qualification remain UNVERIFIED until their own evidence exists.

Use exactly eight small pure-call cases: complete manual-style ten-step PASS,
three admission contradictions, three valid incomplete prefixes preserving prior
FAIL and one malformed prefix demoting the stream. Review the exact clean commit;
run the single final full suite/build/fresh install and source-wheel binding only
after all required slices receive CODE_ACCEPT. Actual manual runtime wiring and
required native/public E2E remain separately verified dependencies.
