# FEBio Gmsh Launch Configuration - Design

## 1. 目的

FEBio Studio上ではCAD面を使って材料、境界条件、面荷重、接触面、解析ステップを設定し、
解析開始時だけ外部Gmshで高品質な曲面Tet10解析メッシュへ置換する。

統合ランチャーは次を一連の処理として実行する。

1. FEBio Studioが出力した参照FEBを受け取る。
2. モデル別設定から元STEPとGmsh条件を読み込む。
3. CAD面選択を参照Tet4のSurface Setとして固定する。
4. Gmshで曲面Tet10を生成して高次最適化する。
5. Surface Setとパート情報を解析メッシュへ転写する。
6. FEBioの積分則に対応するJacobian品質ゲートを通す。
7. 合格した解析FEBをFEBio 4.12でヘッドレス実行する。
8. 別ウィンドウに統合ログを表示する。
9. 成功時だけFEBio Studioへ完成XPLTを返す。

## 2. 初期スコープ

初期版で対応するもの:

- Windows上のFEBio Studio 3.1系とFEBio 4.12系。
- FEBio StudioのLaunch Configurationからの起動。
- 単一STEPファイルから読み込まれた一つ以上のCADパート。
- CAD面およびパートを参照する選択。
- 固定などの面境界条件、面荷重、接触面、材料・ドメイン割当。
- 境界条件転写専用の一次Tet4参照メッシュ。
- Gmsh 4.15.2による曲面Tet10と高次最適化。
- FEBio G8積分点のTet10 Jacobian検査。
- 不正な曲面Tet10に対する局所的な曲率緩和。
- 別ウィンドウのリアルタイムログ、キャンセル、成功・失敗表示。
- 成功したXPLTのFEBio Studioへの読込。

初期版で対応しないもの:

- CADエッジ、CAD頂点、単独ノード選択の転写。
- シェル、ビーム、離散要素の再メッシュ。
- 実行後のGmshメッシュをFEBio StudioのCADモデルへ戻して再編集する処理。
- クラウド実行、リモートキュー、複数ジョブの並列実行。
- STEP形状の自動デフィーチャー。
- 負のJacobianを残したまま解析を続行するオプション。

## 3. アーキテクチャ

ネイティブFEBio Studioプラグインは作らない。FEBio Studioから通常のソルバーに見える
WindowsランチャーをLaunch Configurationへ登録する。

コンポーネントは以下のとおり。

### 3.1 Launcher

- FEBio Studioから入力FEB、ログ、XPLTなどの引数を受け取る。
- モデル別設定を解決する。
- 確認画面を表示する。
- Mesher、Translator、Solverを順番に起動する。
- 子プロセスを監視し、キャンセル時に対象ジョブだけ停止する。
- 成功時は0、失敗時は非0の終了コードをFEBio Studioへ返す。

### 3.2 Reference Model Reader

- FEBio Studioが参照Tet4から出力したFEBを読む。
- Mesh、MeshDomains、Surface、NodeSet、ElementSetを解析する。
- 材料、ステップ、境界条件、荷重、接触、制約、出力設定を保持する。
- 解析コンポーネントから参照されるSurface Setとパートだけを転写対象にする。

### 3.3 Gmsh Mesher

- 設定されたSTEPをOpenCASCADE経由で読み込む。
- 一次Tet4を生成して通常の四面体最適化を行う。
- Tet10へ変換し、曲面へ追従させる。
- `HighOrder`および`HighOrderElastic`最適化を適用する。
- 節点、Tet10、境界Tri6、CADエンティティ分類を中間データへ出力する。

### 3.4 Selection Transfer

- 参照Surfaceの三角形から空間検索構造を構築する。
- Gmsh境界面を距離、法線方向、境界輪郭、面積で分類する。
- 参照Surfaceごとに対応するGmsh Tri6集合を生成する。
- パート割当をGmsh volume entityからMeshDomainへ変換する。
- 未転写、重複、許容差外、面積保存違反があれば処理を停止する。

### 3.5 Quality Gate

- 全Tet10についてFEBio G8積分点の`det(J)`を計算する。
- `det(J) <= 0`を一つでも検出した場合はそのまま解析しない。
- 不正要素が境界上の中間節点投影だけに起因する場合、次式で曲率を緩和する。

```text
x(alpha) = x_mid + alpha * (x_cad - x_mid), 0 <= alpha <= 1
```

- 隣接する全Tet10が品質条件を満たす最大の`alpha`を二分探索する。
- 設定された最大補正量を超える場合は自動修正せずFAILにする。
- 修正後は全要素を再検査する。
- コーナーTet4が反転または極端に劣化している場合は局所曲率緩和を行わずFAILにする。

### 3.6 FEB Translator

- 参照FEBのMeshと選択セットを解析用Gmshメッシュへ置換する。
- 材料、ステップ、境界条件、荷重、接触、制約、出力設定を維持する。
- GmshとFEBioのTet10およびTri6節点順序を明示的に変換する。
- 解析FEBを一時ファイルへ書き、検証後にジョブパスへアトミックに昇格する。

### 3.7 Solver and Log Window

- FEBio 4.12を非表示の子プロセスとして実行する。
- Gmsh、転写、品質検査、FEBioのログを一つの別ウィンドウへ時系列表示する。
- WARNINGとERRORを色分けし、現在のステージと経過時間を表示する。
- キャンセル時は部分XPLTを完成結果として扱わない。
- 失敗時はログウィンドウを保持する。
- 成功時は完成したXPLTを期待パスへ配置して0で終了する。

## 4. モデル別設定

FSMと同じディレクトリへ次のファイルを置く。

```text
<model-name>.gmsh-run.json
```

設定例:

```json
{
  "schema_version": 1,
  "step_path": "C:\\path\\model.step",
  "step_sha256": "...",
  "gmsh": {
    "target_size_mm": 2.0,
    "min_size_mm": 0.1,
    "curvature_elements_per_2pi": 20,
    "algorithm_3d": 10,
    "high_order_optimize": true
  },
  "surface_transfer": {
    "distance_tolerance_mm": 0.02,
    "normal_angle_tolerance_deg": 20.0,
    "relative_area_tolerance": 0.01
  },
  "quality": {
    "reject_nonpositive_jacobian": true,
    "max_midside_correction_mm": 0.01
  }
}
```

STEPのSHA-256と形状指紋が一致しない場合は確認画面から先へ進めない。

ランチャーは設定を次の順で解決する。

1. Launch Configurationから明示された`--config`。
2. 入力FEBと同じディレクトリの`<model-name>.gmsh-run.json`。
3. 入力FEBの親ディレクトリを最大3階層まで探索した同名設定。

候補が複数ある、またはモデル名が一致しない場合は推測で選ばず`CONFIG_ERROR`にする。

## 5. 確認画面

Run開始時に以下を表示する。

- FSM/FEBモデル名。
- STEPパス、ハッシュ、形状指紋の一致状態。
- 目標・最小要素サイズ。
- 曲率分割数と高次最適化の有効状態。
- 参照節点数、Tet4数、転写対象Surface数、パート数。
- 前回実績がある場合はGmsh Tet10数、最大メモリ、メッシュ時間、解析時間。
- ジョブ出力先。

不一致または未設定があればStartを無効にする。Cancelは入力モデルを変更せず終了する。

## 6. データフロー

```text
FEBio Studio FSM
  -> 参照Tet4 FEB export
  -> Launch Configuration
  -> preflight dialog
  -> reference FEB parse
  -> STEP/Gmsh Tet4
  -> curved Tet10 optimization
  -> surface and part transfer
  -> FEBio G8 quality gate
  -> analysis FEB
  -> FEBio headless
  -> completed XPLT
  -> FEBio Studio result open
```

解析開始後のCAD選択はスナップショットであり、FEBio Studio側を変更した場合は新しいジョブを
開始する。

## 7. ジョブと成果物

各実行は次へ保存する。

```text
<model-dir>\jobs\<model-name>\<run-id>\
```

内容:

```text
reference.feb
settings.snapshot.json
preflight.json
gmsh.msh
gmsh-report.json
surface-transfer.json
quality-report.json
analysis.feb
febio.log
result.xplt
run-manifest.json
```

入力FSM、STEP、参照FEBは上書きしない。完成前のファイルには`.partial`を付け、成功時だけ
最終名へ変更する。

## 8. Surface転写の合格条件

各転写対象Surfaceについて次を満たす。

- Gmsh境界要素が少なくとも一つ割り当てられる。
- 各境界要素の複数サンプル点が参照Surfaceの許容距離内にある。
- 法線差が設定角度以内である。
- 参照Surface面積とGmsh Surface面積の相対差が許容値以内である。
- 同一境界要素が排他的なSurfaceへ重複割当されない。
- 接触ペアの主面・従面が空にならない。

許容差外の自動最近傍割当は行わない。

## 9. エラー処理

エラー分類:

- `CONFIG_ERROR`: 設定、STEP、実行ファイルが不正。
- `REFERENCE_ERROR`: 参照FEBまたは選択が解析できない。
- `GMSH_ERROR`: STEP読込、Tet4、Tet10、高次最適化が失敗。
- `TRANSFER_ERROR`: Surfaceまたはパート転写が不完全。
- `QUALITY_ERROR`: Jacobianまたは品質条件が不合格。
- `FEB_TRANSLATION_ERROR`: FEB生成または節点順序検証が失敗。
- `SOLVER_ERROR`: FEBioが非0終了、初期化失敗、または結果未生成。
- `CANCELLED`: ユーザーがキャンセル。

失敗時は元モデルを変更せず、部分結果を開かず、再現コマンドと原因をログへ残す。

## 10. テスト

### 10.1 単体テスト

- 設定スキーマとパス解決。
- Gmsh/FEBioのTet10、Tri6節点順序変換。
- 参照FEBの保持・置換セクション。
- Surface距離、法線、面積判定。
- 重複・未転写Surfaceの拒否。
- Tet10 G8 Jacobian。
- 曲率緩和の最大`alpha`探索。
- ログ分類と終了コード。

### 10.2 統合テスト

- 小型STEP、一つの固定面、一つの面荷重。
- 複数の分離Surface Set。
- 重複選択を許す異なる解析コンポーネント。
- 接触ペア。
- STEPハッシュ不一致。
- 意図的に反転させたTet10。
- Surface転写許容差外。
- FEBio初期化失敗。
- キャンセルと部分成果物処理。

### 10.3 実機スモークテスト

1. 小型CADをFEBio Studioへ読み込む。
2. 一次Tet4参照メッシュを作る。
3. CAD面で境界条件と荷重を設定する。
4. `Gmsh -> FEBio` Launch Configurationを選ぶ。
5. 確認画面の値とSurface数を確認する。
6. Gmsh Tet10、転写、品質ゲートを通す。
7. 別ログウィンドウでFEBio実行を確認する。
8. 完成XPLTが同じFEBio Studioで開くことを確認する。

同一ウィンドウへの自動読込は最初の技術スパイクで確認する。Launch Configurationだけでは
成立しない場合は、結果ファイルを新しいStudioで開く実装へ進まず、設計を再確認する。

### 10.4 回帰モデル

`02_Bottom_Frame_FEBio_Tet10.fsm`を参照モデルとする。

- CAD面選択から出力されたSurface Setを維持する。
- Gmsh曲面Tet10を生成する。
- FEBio G8で負のJacobianを0にする。
- FEBio 4.12のモデル初期化を通す。
- 全10ステップ完走は性能・解析条件の別検証として記録する。

## 11. 完了条件

- Launch Configurationから確認画面を起動できる。
- STEPと設定の不一致を解析前に拒否できる。
- 参照Tet4からCAD面・パート選択を解析用Gmsh Tet10へ転写できる。
- 材料、ステップ、境界条件、荷重、接触、制約、出力設定を維持できる。
- 全Tet10がFEBio G8品質ゲートを通る。
- 不正な曲面中間節点を許容差内で局所修正できる。
- 別ウィンドウにリアルタイムログを表示できる。
- 失敗・キャンセル時に部分XPLTを結果として開かない。
- 成功時に完成XPLTを元のFEBio Studioで開ける。
- 単体・統合テストが通る。
- 小型モデルの実機スモークテストが通る。
- Bottom Frame回帰モデルがFEBio初期化を通る。
