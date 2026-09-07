# P0-B FEBio Studio XPLT観察記録

日付: 2026-09-07 / ブランチ: `codex/p0-b-studio-observation` / 観察開始base: `f903575283136ffe45ff0c703bc3f6fab3462285` / remote: `https://github.com/A6721jpn/cae-harness.git`（`REMOTE_CONFIGURED`）
追跡変更: このMarkdownのみ。全UI・画像・native入力・metadataはignoredな`.local/verification/P0B-studio-observation-01`に保存した。

## 1. 判定

固定したsynthetic native XPLTのコピーを、既存の外部アプリFEBio Studioで読み込み、2 stateを表示し、final stateの`X - displacement` color fieldをレンダリングできた。したがって今回のbounded observationは`CONFIRMED`である。

これはFEBio Studioのこのartifactに対する観察であり、製品XPLT adapter、圧縮XPLT、任意のFEBio version、FBS、real model、solver、PreviewReceipt authority、universal compatibilityを証明しない。

## 2. 入力と実行環境

| 項目 | 実測値 |
|---|---|
| source XPLT | `C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-elastic-patch-01\attempt-06\elastic-patch.xplt` |
| Studio用copy | `C:\Users\backo\.codex\worktrees\e081\CAE-harness\.local\verification\P0B-studio-observation-01\elastic-patch.xplt` |
| XPLT bytes / SHA-256 | `8966` / `0B835386CF2606BFD408379DB9A99D2696307C62D321A13D11D9D3488F4094A4`（source/copy一致） |
| executable | `C:\Program Files\FEBioStudio\bin\FEBioStudio.exe` |
| executable bytes / SHA-256 | `38265856` / `703AE324AE46AB03E9E5389116FA90AF5EFFE39364ED136DD6041AB8264252AE` |
| UI version | `FEBio Studio 3.1.0`（Help > Aboutの画面で確認） |
| PE VersionInfo | file/product/name/descriptionは空欄。versionをPE metadataから推定していない |
| process/window | PID `51464`, window `2499284`, title `FEBio Studio 3.1.0` |

Studioは観察開始時に未起動だったため、返却されたapp idから新しい自身のinstanceを起動した。既存のFusion360、Slack、Chrome、Mayo等のwindow/documentは操作していない。

## 3. UI観察の状態遷移

UI操作は指定のWindows Computer Use skill（`@oai/sky`）だけで行い、各inputの直後にwindow stateを再取得した。file chooserのaccessibility indexが安定しない箇所だけ、直前のscreenshot-backed coordinateとrefreshで回復した。deliberate UI actionはrecoveryを含め`18`回で、上限`30`以内である。

| status | UTC | UIで確認した事実 | screenshot / SHA-256 |
|---|---|---|---|
| `LAUNCHED` | `2026-09-07T06:17:30.339Z` | Welcome画面、`FEBio Studio Version 3.1.0`、対象file未ロード | [initial-welcome.png](C:/Users/backo/.codex/worktrees/e081/CAE-harness/.local/verification/P0B-studio-observation-01/initial-welcome.png) / `49604EAC3EEC989C8F9528E825E3341F8D1F8BA12AF212F2A9AB0D4C6F62D6EE` |
| `FILE_LOADED` | `2026-09-07T06:21:25.146Z` | Project/Post treeに`elastic-patch.xplt`、Import dialogで`Read all states`、timeline `1/2`、viewport `Time = 0`、mesh表示、error dialogなし | [file-loaded.png](C:/Users/backo/.codex/worktrees/e081/CAE-harness/.local/verification/P0B-studio-observation-01/file-loaded.png) / `39683A41BD5D55D37793257C348BA044A264FFB41D67FE0D49311974FEB36201` |
| `FINAL_FIELD_DISPLAYED` | `2026-09-07T06:23:03.802Z` | timeline `2/2`、viewport `Time = 1`、selector `X - displacement`、viewport `x displacement (m)`、color map付きmesh、error dialogなし | [final-x-displacement.png](C:/Users/backo/.codex/worktrees/e081/CAE-harness/.local/verification/P0B-studio-observation-01/final-x-displacement.png) / `02270B861E4E930BD3A90FE4A03C364EDE62B851BFF828F3702ED75CDB9EBE46` |
| `STUDIO_VERSION_UI` | `2026-09-07T06:24:09.885Z` | Help > Aboutに`FEBio Studio`, `Version 3.1.0` | [about-version.png](C:/Users/backo/.codex/worktrees/e081/CAE-harness/.local/verification/P0B-studio-observation-01/about-version.png) / `D9A512C89BAF5381DB41373F477BE940070CE522D000124A63D53CB6F6B814F5` |

最終画面で表示されたx方向のrangeを画像の見た目から数値化していない。63 nodes / 24 Tet10のcount/typeも、このStudio UI状態では明示表示されなかったため、Studio観察の事実としては主張しない。stress/sxxは未観察であり、不要なgeneric viewer testを追加していない。

## 4. 手順と証跡

1. cleanな`6e027f6d667769d16077b46bc495b4d1f4fd2fb0`を確認し、`git merge --ff-only f903575283136ffe45ff0c703bc3f6fab3462285`を実行してbaseを固定した。
2. `codex/p0-b-studio-observation`を同じworktreeに作成し、immutable source XPLTを新規ignored directoryへcopyした。copyのbytes/SHAはsourceと一致した。
3. 返却されたFEBioStudio app idから起動し、Welcome画面を保存した。
4. Studio UIのOpen Model Fileからcopyのabsolute pathをfile name fieldへ入力し、`Read all states`でimportした。Project/Post treeのbasename `elastic-patch.xplt`、timeline `1/2`、`Time = 0`を保存した。
5. `last` time-stepを選択して`2/2` / `Time = 1`にし、data-variable menuから`displacement > X - displacement`を選択した。`x displacement (m)`とcolor map付きrenderを保存した。
6. Help > Aboutを開いてUI version `3.1.0`を保存し、観察用About dialogだけを閉じた。Studio instanceはfinal field表示のまま残した。

ignored manifestと各状態の詳細JSONは[studio-environment.json](C:/Users/backo/.codex/worktrees/e081/CAE-harness/.local/verification/P0B-studio-observation-01/studio-environment.json)、[file-loaded-observation.json](C:/Users/backo/.codex/worktrees/e081/CAE-harness/.local/verification/P0B-studio-observation-01/file-loaded-observation.json)、[final-field-observation.json](C:/Users/backo/.codex/worktrees/e081/CAE-harness/.local/verification/P0B-studio-observation-01/final-field-observation.json)、[about-version-observation.json](C:/Users/backo/.codex/worktrees/e081/CAE-harness/.local/verification/P0B-studio-observation-01/about-version-observation.json)に保存した。

### 境界と未検証事項

- Studioは既存external viewerとして使っただけであり、product GUI/runtime dependencyは追加していない。
- solver、Gmsh、FBS、FEBio Studioのnative backend、plugin/DLL/config identity、real `02_CAE` dataは実行・変更・主張していない。
- 元のreader、native XPLT、旧artifact、product `src/` / `tests/` / deps / common contracts / authority docsは変更していない。
- synthetic XPLTの読込・表示は、物理的正しさ、実部品の許容値、製品全体の互換性を意味しない。

## 5. 検証・ゲート

これは新規product implementationではなく、外部viewerのread-only observation reportである。

| gate | result |
|---|---|
| docs-only RED | `N/A`（product code/test contractの変更なし） |
| docs-only GREEN | `N/A`（UI observation evidenceでありproduct testではない） |
| required product tests / format / lint / type / build / installed smoke | `N/A`。実行済み成功とは数えない |
| CAE boundary / real E2E | `N/A`。real solver/FBS/BottomFrameは未実施 |
| evidence hash check | source/copy、executable、4 screenshotsのSHA-256一致をread-only shell checkで確認、exit `0` |
| report diff check | `git diff --check` exit `0` |
| local links | reportの相対/絶対local linksを確認、欠落 `0` |

最終handoffのcommit SHA、parent SHA、clean statusはPMへのhandoff messageとGitの最終確認で報告する。この文書自身には自己参照になるcommit hashを埋め込まない。
