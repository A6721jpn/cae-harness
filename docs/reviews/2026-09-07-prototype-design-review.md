# プロトタイプ設計文書のレビュー記録

> 過去記録：本文は記録対象時点の証拠を保持する。旧体制・次タスクの記載は現行の作業指示ではない。現行体制は[記録の扱い](README.md)と[開発契約](../../AGENTS.md)に従う。

日付: 2026-09-07 / 対象: 設計仕様書0.1と実装・検証計画0.1。
この記録は文書検査の証拠であり、製品仕様の決定元ではない。

## 作成したファイル

- [設計仕様書](../specs/2026-08-27-febio-llm-cae-harness-design-v2.md)
- [実装・検証計画](../plans/2026-08-27-febio-cae-harness-greenfield-plan.md)
- [README](../../README.md)
- [AGENTS.md](../../AGENTS.md)
- [.gitignore](../../.gitignore)
- 本レビュー記録

## レビュー内容

STEP部品と生成剛体の接触解析を初期範囲に含め、材料・支持・摩擦等の未確定値を推定で補わない契約を確認した。ケース版、質問世代、実行bundle、結果manifestの責務を分け、再解析と比較を同じデータ契約へ結び付けた。

runの実行成立、数値品質、物理モデルの適用根拠、Studioの読込確認を別々に定義した。出力の存在・プロセス起動だけでは完了とならず、改変や中断時に未完了へ戻す条件を確認した。

P0の能力確認、P1の契約固定、P2〜P6の実装、P7の許可済み実モデル受け入れを定義した。標準pytest、実ネイティブ試験、Studioの人による確認、実モデルE2Eを区別した。

参照値の算術検算は、弾性パッチの0.100 N、Hertz接触の0.00463 Nを対象とした。これらは合成検証ケースの解析的な参照値であり、FEBioの計算結果ではない。数値許容値は検証方針として明示され、実部品の物理的許容値へ流用しない。

## 文書検査

実行cwd: `C:\dev\CAE-harness\CAE-HARNESS-V2`。
ローカルの一時検査器を使い、次のコマンドで27チェック合格、0不合格、exit code 0を確認した。製品テストの件数ではない。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\dev\CAE-harness\CAE-HARNESS-V2\.local\verification\design-docs\check_design_docs.ps1'
```

検査対象は6ファイル、20要求、19ゲート、P0〜P7の8工程。ローカルリンク16件と出典URL6件の形式、相互参照、見出し階層、表の列数、コードフェンス、UTF-8、未解決の編集トークン、必須ゲート記載、参照値の丸めを検査した。URLの形式検査は外部ページの全文取得やローカル実機適合性の検証を意味しない。

初回は一時検査器の文字コード解釈によりPowerShell 5.1が構文解析エラーで終了した（exit code 1、検査未成立）。検査器へUTF-8 BOMを付けた後に再実行した。文書や製品のRED証拠として数えていない。一時検査器とJSON結果は`.local/verification/design-docs/`に保存し、Gitには含めない。

Git差分の空白検査は`git diff --cached --check`を実施し、exit code 0。製品のpytest・ruff・mypy等をこの文書検査で代用していない。

## 対象コミットと文書ハッシュ

| 対象 | commit SHA |
|---|---|
| 設計仕様書・開発契約・gitignore | `37ea989912cbe24484b194ecd8dc045d20b6b41d` |
| 実装・検証計画 | `5ff79b3a13abfcd2685bb21100a39183b058aa2e` |

| 文書 | 作成時のUTF-8ファイルのSHA-256 |
|---|---|
| 設計仕様書 | `83448dfdaf15877c9fd98ae0155888079ab47b4410d4963de822b95247136163` |
| 実装・検証計画 | `44368a135ca6b4765566f9605865a95b116a51624cf1febe06ba0feb33a92cc5` |

本レビュー記録とREADMEは上記コミットに続く文書コミットに含める。リポジトリの最終HEADは納品時の報告で示す。改行変換を伴うcheckoutではファイルのバイトハッシュが変わり得るため、履歴の同定にはcommit SHAも用いる。

## 未実施・未検証

製品実装、製品テストのRED/GREEN、pytest、ruff、mypy、製品CAE境界スキャナー、build、installed smokeは未実施。実FEBio・Gmsh・Studioのローカル版と互換性、実LLM経路、実モデルと最終BottomFrame E2Eも未検証。

本作業では実CAEモデル、結果、認証情報、デスクトップ状態を取得・変更していない。独立した新規Gitリポジトリ内に文書を作成し、remoteは設定していない。状態は`REMOTE_PENDING`。

次タスクはP0の骨格・互換性検証。実装・検証計画のREDコマンドから開始する。
