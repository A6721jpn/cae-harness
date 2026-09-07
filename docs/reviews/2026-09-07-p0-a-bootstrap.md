# P0-A bootstrap verification

日付: 2026-09-07  
対象: FEBio CAE Harness V2 の P0-A（Python 3.12 headless CLI bootstrap）  
判定範囲: version、doctor、CAEデータ境界スキャナー、wheel配布、installed smoke  

この記録はP0-Aの合成・ローカル証拠であり、実FEBio、公式FBS、FEBio Studio、Gmsh、実モデルの成功証拠ではない。製品仕様の決定元は設計仕様書と実装・検証計画であり、本書は証拠と未検証事項だけを記録する。

## 固定したGit境界

| 項目 | 値 |
|---|---|
| 作業ブランチ | `codex/p0-a-bootstrap` |
| 固定base | `0cc803a6c5a568172662592e5ec932b9c7d09112` |
| root commit | `37ea989912cbe24484b194ecd8dc045d20b6b41d` |
| 候補コードSHA | `3782d1b7aa37d2774f6e9e1971977f35477fd02e` |
| remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote状態 | `REMOTE_CONFIGURED` |
| remote `V2` SHA | `0cc803a6c5a568172662592e5ec932b9c7d09112` |
| 作業終了時のGit status | clean（ignoredの`.local/`, `build/`, `dist/`, `*.egg-info/`を除く） |

コミット列は次のとおりである。

1. `33bb25850c94cd775c1625b6eab82d483e4f7af7` — P0-Aテストのみ。
2. `902a44e5429240213c8c02761dc165ef86e7f514` — P0-Aテストのformatのみ。
3. `c7644f7ba00c9eeb28d3c48111f103736321ce50` — CLI、doctor、scanner、packagingの最小実装。
4. `3782d1b7aa37d2774f6e9e1971977f35477fd02e` — scannerの除外パス報告を修正。

baseから候補コードSHAまでの変更は、`pyproject.toml`、`src/febio_cae/**`、`scripts/scan_cae_data.py`、`tests/unit/test_bootstrap.py`、`tests/unit/test_scan_cae_data.py`だけである。`.gitignore`、設計仕様書、実データ領域は変更していない。

## RED / GREEN

実行インタープリターは `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe`、Python `3.12.10` である。

### RED

```powershell
python -m pytest tests/unit/test_bootstrap.py tests/unit/test_scan_cae_data.py --basetemp .local/verification/P0-A-red-02
```

結果: 5 tests collected、5 failed、exit code 1。`febio_cae`未実装と`scripts/scan_cae_data.py`未実装による期待動作のassertion failureであり、collection errorではない。

`P0-A-red-01`はbasetempの親ディレクトリを先に作らなかったためWindows setup errorが2件発生した。これは環境証拠として採用せず、親を作成した未使用の`P0-A-red-02`でREDを再収集した。

### GREEN

```powershell
python -m pytest tests/unit/test_bootstrap.py tests/unit/test_scan_cae_data.py --basetemp .local/verification/P0-A-green-02
```

結果: 5 passed、exit code 0。

全体ローカルpytestも候補SHAでfresh basetempを使用した。

```powershell
python -m pytest --basetemp .local/verification/P0-A-final-01
```

結果: 5 passed、exit code 0。標準testpathsは`tests/unit`と`tests/component`であり、実行対象は5件だった。

## 実装した契約

- `python -m febio_cae --version` とインストール後の`febio-cae --version`は`febio-cae 0.1.0`を返す。
- `doctor --json`は`schema_version`、能力状態、診断、次のアクションをJSONで返す。FEBio、FEBio Studio、Gmshのいずれかが不足すると`status=UNAVAILABLE`、exit code 4となる。PATHを空にした合成テストでこの契約を確認した。
- doctorで見つかったネイティブ実体は`FOUND_UNVERIFIED`であり、存在確認を実機互換性の検証結果へ昇格させない。
- `scripts/scan_cae_data.py --root <root>`はtracked pathとfilesystemを検査し、`02_CAE`、CAE結果・モデル拡張子、credential/token/desktop状態、trackedされた除外領域を拒否する。`.git`、`.local`、build/dist、cache、venv等の除外を結果へ明示する。

## 必須ローカルゲート

| コマンド | 結果 |
|---|---|
| `python -m pytest --basetemp .local/verification/P0-A-final-01` | 5 passed / exit 0 |
| `python -m ruff format --check .` | 14 files already formatted / exit 0 |
| `python -m ruff check .` | All checks passed / exit 0 |
| `python -m mypy src tests` | Success, 8 source files / exit 0 |
| `python scripts/scan_cae_data.py --root .` | `PASS`, 16 files checked、16 tracked、0 issues / exit 0 |
| `python -m build` | sdistとwheelをbuild / exit 0 |

最終scanner出力では`git_tracking.available=true`、`excluded_tracked_files=0`であり、除外領域は`.git`、`.local`、cache、`__pycache__`、`dist`、`febio_cae.egg-info`として報告された。
上記の16ファイル結果は候補コードSHAでのゲートである。証跡文書を追加した後も同じscannerを再実行し、17 files checked、16 tracked、0 issues、exit code 0を確認した。

## wheel と installed smoke

buildで一意に選択したwheel:

```text
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 8C77E0659980757E8DDAD76944F7624287824F4957C2C21D062E4BE3E66D7F61
```

未使用の`.local/verification/P0-A-installed-02`へPython 3.12のclean venvを作り、wheelをnon-editableで`--no-index --no-deps`インストールした。installed cwdは次のとおりである。

```text
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-installed-02\installed-cwd
```

実行結果:

- `febio-cae.exe --version` → `febio-cae 0.1.0`, exit code 0。
- `import febio_cae`の実体 → `...installed-env\Lib\site-packages\febio_cae\__init__.py`、exit code 0。
- import時のPython → `...installed-env\Scripts\python.exe`。

初回`P0-A-installed-01`はPowerShell 5.1の`New-Item -LiteralPath`引数差異でinstalled cwdを作成できなかったため、wheel smokeの合格証拠には数えていない。別のclean venvと隔離cwdで`P0-A-installed-02`を再実行した。

## 未検証事項と次タスク

- FEBio、FEBio Studio、Gmshの実体・版・ハッシュ・CLI引数・入力形式・XPLT/FBS互換性は未検証。doctorの不足能力は環境診断であり、P0-Bのnative compatibility evidenceではない。
- 公式FBS、XPLT reader、Studio読込確認、実solver、実LLM、native/E2E-01、許可済み実モデルE2E-02、最終BottomFrame E2E-03は未実施。
- scannerはパス、拡張子、Git追跡境界を検査する合成/local gateであり、全ファイル内容の安全性を保証するものではない。
- wheel smokeは配布されたCLIのversion/importだけを確認し、解析経路や実モデル成功を意味しない。

次タスクはPMがP0-A候補SHAを独立Astra Mediumレビューへ渡し、レビュー済みのclean commit列をV2へ統合すること。P0-Bでは別の許可された範囲でFEBio、Studio、Gmshの実機probeと対応表を作成する。
