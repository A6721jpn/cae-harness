# FEBio CAE Harness V2

STEP部品に新しく生成した剛体治具を接触させ、押し込みによる変形を解析するプロトタイプ。自然言語による条件設定、FEBio実行、XPLTプレビュー、部分編集と再解析比較を対象とする。

現在は設計文書の段階であり、CLI・解析機能は未実装。Python 3.12のheadless CLIを開発し、プレビューには外部のFEBio Studioを使用する。

## 開発文書

| 文書 | 内容 |
|---|---|
| [設計仕様書](docs/specs/2026-08-27-febio-llm-cae-harness-design-v2.md) | 20要求、構成、データ契約、接触モデル、状態遷移、品質、CLI、比較、最適化の拡張点 |
| [実装・検証計画](docs/plans/2026-08-27-febio-cae-harness-greenfield-plan.md) | P0〜P7、RED/GREEN、19検証ゲート、数値参照ケース、配布・実モデル受け入れ、要求対応表 |
| [開発契約](AGENTS.md) | 作業範囲、データ境界、テスト先行、必須ゲート、報告規則 |
| [文書レビュー記録](docs/reviews/2026-09-07-prototype-design-review.md) | 文書検査と未検証項目 |

製品仕様の決定元は設計仕様書と実装・検証計画の2件である。文書名の2026-08-27は識別子であり、今回の作成日は2026-09-07。

## 次の作業

P0で最小CLI・配布骨格をテスト先行で作り、実際のFEBio・Studio・Gmshの互換性を合成形状で確認する。実部品の物理条件はケース実行時に根拠から確定する。

実CAEモデル・結果・認証情報はGitへ含めない。remoteは未設定で、状態は`REMOTE_PENDING`。
