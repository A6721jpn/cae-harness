# FEBioワークスペース移行検証報告

- 実施日: 2026-07-30
- 対象: `C:\Users\backo\OneDrive\Documents\FEBio`
- 方針: ツール開発と実CAE解析を物理分離し、既存ファイルは削除せず分類する

## 移行結果

| 領域 | 用途 | Git/GitHub |
|---|---|---|
| `01_Tools/febio-tools` | 再利用可能なツール、テスト、ツール文書 | Git必須。公開先はtool-only GitHubに限定 |
| `01_Tools/_worktrees` | ツール開発用linked worktree | canonical repoに接続 |
| `01_Tools/_migration_backup` | 旧Git、旧worktree、旧ルート配置の保全 | 通常作業禁止 |
| `02_CAE/01_Active` | 解析中の案件 | Git/GitHub禁止 |
| `02_CAE/02_Archive` | 完了ゲートを通過した解析 | Git/GitHub禁止 |
| `02_CAE/90_Needs_Review` | 案件または成果物の要確認 | Git/GitHub禁止 |
| `02_CAE/98_Delete_Review` | 削除候補の隔離と台帳 | 明示承認まで削除禁止 |
| `02_CAE/99_Temporary` | 案件未確定の旧一時物 | 内容分類後に各案件へ移す |

ルートには`AGENTS.md`と`README.md`を置き、ツール側とCAE側にはさらに厳しい
下位`AGENTS.md`を配置した。今後のLLMは最寄りの規約を読み、実モデルをツールrepoへ
入れず、CAE配下でGitを実行しない。

## 既存ファイルの移行先

- 0724の修復試行は
  `02_CAE/90_Needs_Review/Bottom_Frame/2026-07-24_0724A_repair-trial`へ移した。
- 0728のTet10準備案件は
  `02_CAE/01_Active/Bottom_Frame/2026-07-28_0728C_tet10-preparation`へ移した。
- local refinement、fatigue recovery、VM/FOS、M2 contact、local040 screwの
  文書・LLM実行履歴は、それぞれ独立したActiveケースへ移した。
- 材料カードは`02_CAE/00_Shared/Materials`へ移した。
- 旧`tmp`は`02_CAE/99_Temporary/Legacy_tmp`へ隔離した。
- 再利用スクリプトとランチャーは`01_Tools/febio-tools`へ履歴付きで再編した。
- 旧ルートのコピー、旧worktree、旧Gitメタデータは削除せず
  `01_Tools/_migration_backup`へ保全した。

## データ保全検証

- 移行前インベントリ: 18,838ファイル。
- 永久保存候補として事前記録した92ファイルを、サイズで候補を絞った後に
  SHA-256で再照合した。
- 結果: **92/92一致、欠損0**。
- 個別対応表:
  `docs/workspace-organization/permanent-files-reconciliation.csv`
- CAEケース: 8件。
- ケースマニフェスト記載ファイル: 73件。
- マニフェストの存在・サイズ・SHA-256不一致: 0件。
- CAE配下の`.git`ディレクトリまたはGit pointer file: 0件。
- CAE内の保存形式: FSM 3件、FEB 1件、XPLT 0件。XPLTは元データにも存在しなかった。

## Gitとworktreeの検証

- canonical repo: `01_Tools/febio-tools`
- 移行時HEAD: `8d40b7ac1f07bce1b970dac7e7df7434843faa30`
- 旧Gitの更新済みbundleは全参照を含むことを`git bundle verify`で確認した。
- `01_Tools/_migration_backup/legacy-root-git`は
  `git fsck --full`成功、HEAD `4881842fa7599a0b823b1366f74ed9dfdc45992f`。
- 次のlinked worktreeを新しいcanonical repoへ接続した。
  - `codex/febio-cad-tet10-repair`
  - `feature/fusion-step-repair-monitor`
  - `codex/local040-rigid-screw-cylinder`
- 旧worktreeから552ファイルを復元し、全ファイルのSHA-256が一致した。
- Fusionの未コミットbinary diffは事前パッチSHA-256と一致した。
- 移行中にlocal040へ追加された4件のLLM実行記録も検出し、worktreeに残したまま
  CAEケースの`05_Verification/LLM_History`へ追補した。

現在remoteは意図的に未設定である。旧bundleはCAE履歴を含むためGitHubへ
接続・pushしてはならない。次のツール開発開始時に、LLMがtool-only GitHub
repositoryを確認または作成し、そのURLだけをremoteへ設定する。

## 削除候補

`02_CAE/98_Delete_Review/2026-07-30/DELETE_CANDIDATES.csv`に6項目を記録した。
今回の移行ではファイルを削除していない。

- 再生成可能なキャッシュ: 2項目
- canonical FSMとハッシュ一致する重複: 1項目
- 空ディレクトリ: 2項目
- 追加分類が必要な旧tmp: 1項目、3,189ファイル、218,385,897 bytes

旧tmpにはFSM/FEB/XPLTはないが、STEP 8件、PDF 6件、スクリプト、組込みPython
環境が混在するため、一括削除候補にはしていない。

## テスト

- 共通ツール・境界ガード: `13 passed, 1 skipped`
- FEBio Gmsh launcher: `44 passed, 1 skipped`
- CAE workspace guard: exit code 0
- ルートと`02_CAE`の`git rev-parse`: exit code 128（Git work tree外）
- `01_Tools/febio-tools`の`git rev-parse`: `true`

## 既知の制約

旧ルート`.git`の本体376,744,354 bytesと残余11ファイルは、ハッシュ統合後に
`_migration_backup`へ保全し、バックアップrepoの`git fsck`も成功した。
ただし、Codex Desktopが開いている間はルートの`.git`ディレクトリエントリに
Windowsの共有ロックが掛かり、最後のディレクトリ名変更だけが拒否された。

ルートに残る`.git`はHEAD、config、index、objectsを持たない**非稼働の残骸**で、
ルートとCAEの`git rev-parse`はいずれも失敗し、CAE workspace guardは通過する。
次回このworkspaceを開くLLMは、旧Gitバックアップとbundleを再検証した後、
ロックが解除されていればこの残骸を
`01_Tools/_migration_backup/legacy-root-git-remainder-2`へ移す。削除はしない。
