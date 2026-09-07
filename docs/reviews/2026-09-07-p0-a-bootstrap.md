# P0-A bootstrap verification

日付: 2026-09-07
対象: FEBio CAE Harness V2 の P0-A（Python 3.12 headless CLI bootstrap）
判定範囲: version、doctor、CAEデータ境界スキャナー、wheel配布、installed smoke

この記録はP0-Aの合成・ローカル証拠であり、実FEBio、公式FBS、FEBio Studio、Gmsh、実モデルの成功証拠ではない。製品仕様の決定元は設計仕様書と実装・検証計画であり、本書は証拠と未検証事項だけを記録する。

## 固定したGit境界

| 項目 | 値 |
|---|---|
| 作業ブランチ | `codex/p0-a-bootstrap` |
| remediation base | `ff4b782900c34c979c6c268598ef23332d32fc7c` |
| test-only SHA | `17841c978792185483cec7b7d7d19f000dba21c3`、`ff87a7a0b41643d7dd8d4f631075334bad292bcc`、`30023b86974b54866c40686f9471c06d0f06ef5b` |
| production fix SHA | `89e5d4638f81368fc237da6a8addde6ba7000c09` |
| final product candidate SHA | `30023b86974b54866c40686f9471c06d0f06ef5b` |
| remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote状態 | `REMOTE_CONFIGURED` |
| remote `V2` SHA | `0cc803a6c5a568172662592e5ec932b9c7d09112` |
| fresh gate実行時のGit status | 各metadataの`dirty_before`/`dirty_after`は空。ignoredの`.local/`, `build/`, `dist/`, `*.egg-info/`を除く |

今回のremediationコミット列は次のとおりである。

1. `17841c978792185483cec7b7d7d19f000dba21c3` — 回帰テストのみ。
2. `ff87a7a0b41643d7dd8d4f631075334bad292bcc` — 回帰テストの型・format補正のみ。
3. `89e5d4638f81368fc237da6a8addde6ba7000c09` — scanner、doctor、pytest markerのproduction fix。
4. `30023b86974b54866c40686f9471c06d0f06ef5b` — Git-root fixtureのテスト補正のみ。

final product candidateまでの変更は、`pyproject.toml`、`src/febio_cae/cli/doctor.py`、
`src/febio_cae/cli/scan_cae_data.py`、`tests/unit/test_bootstrap.py`、
`tests/unit/test_scan_cae_data.py`だけである。`.gitignore`、設計仕様書、実データ領域、
V2へのpushは変更していない。今回の証拠文書更新はこの範囲外の許可された
`docs/reviews/2026-09-07-p0-a-bootstrap.md`だけに限定する。

## RED / GREEN

実行インタープリターは `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe`、Python `3.12.10` である。今回の実行はすべて、同じ作業root `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness` で、runner metadataに展開済みargv、cwd、HEAD、dirty state、UTC start/end、exit code、stdout/stderrの絶対パスを保存した。

### 過去証跡の限定

旧版候補 `3782d1b7aa37d2774f6e9e1971977f35477fd02e` の旧RED（5 collected / 5 failed / exit 1）は、入口未実装・import/entrypoint不在を含む初期骨格の証拠であり、今回のH1/H2/M1/M2/M3に対する厳格な動作REDではない。旧runtime CommandExecution出力に実在したexit codeの補正と、実行時SHAの事前記録が欠けていることは別問題であり、旧REDを今回のfresh証跡へ置き換えたり、元SHAを後付けしたりしない。旧`P0-A-red-01`のWindows setup errorも合格証拠には数えない。

### 今回の動作RED

test-only SHA `17841c978792185483cec7b7d7d19f000dba21c3` で回帰テストを追加した後、次を実行した。

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-fix-red-01
```

結果は `15 collected / 9 failed / 6 passed / exit 1`。失敗はGit unavailable、staged Git-index FEBio XML/private-key、filesystem read error/reparse point、doctorの未検証exit、human doctor、pytest markerの実装前assertionであり、collection/setup errorではない。全ログは `.local/coordination/runs/P0-A-fix-red-01/metadata.json`、`stdout.bin`、`stderr.bin`。metadataの`head_before`/`head_after`は同じ `17841c978792185483cec7b7d7d19f000dba21c3`、dirtyは前後とも空である。

### 今回のGREEN

final product candidate `30023b86974b54866c40686f9471c06d0f06ef5b` で次を実行した。

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-fix-green-02
```

結果は `15 passed / exit 0`。freshログは `.local/coordination/runs/P0-A-fix-green-02/metadata.json`、`stdout.bin`、`stderr.bin`。metadataのHEADは前後とも `30023b86974b54866c40686f9471c06d0f06ef5b`、dirtyは前後とも空である。

標準testpaths（`tests/unit`, `tests/component`）全体のfresh実行も同じcandidateで行い、`15 passed / exit 0`だった。正確なargvとraw outputは、下記gate表の `P0-A-fix-gate-pytest-01` に固定した。

## 実装した契約

- `python -m febio_cae --version` とインストール後の`febio-cae --version`は`febio-cae 0.1.0`を返す。
- `doctor --json`は`schema_version`、`case_id`、`revision_id`、`run_id`、能力状態、診断、次のアクションをJSONで返す。FEBio、FEBio Studio、Gmshのいずれかが不足すると`status=UNAVAILABLE`、exit code 4となる。存在するだけのinert fileは`FOUND_UNVERIFIED`、全能力が未検証なら`status=UNVERIFIED`、検証用next action付き、exit code 4となる。`doctor`のデフォルト出力はhuman-readableで、`--json`だけがmachine-readable出力を選ぶ。
- `scripts/scan_cae_data.py --root <root>`はtracked path、Git index blob、current filesystemを検査し、`02_CAE`、CAE結果・モデル拡張子、credential/token/desktop状態、trackedされた除外領域を拒否する。Git root/indexの取得失敗、blobのread/size超過、未知Git mode、filesystem traversal/read error、symlink/Windows reparse pointは`INCOMPLETE`かつexit code 4で、成功扱いにしない。bounded detectorは明確なFEBio XML root/closeと十分なprivate-key PEM bodyだけを検出し、単なる文書上の言及はfalse positive controlとして許容する。`.git`、`.local`、build/dist、cache、venv等の除外を結果へ明示する。
- `pyproject.toml`は`febio`、`llm`、`native`、`e2e` markerを登録し、native/e2eを標準testpathsへ追加しない。

## 必須ローカルゲート

全gateはfinal product candidate `30023b86974b54866c40686f9471c06d0f06ef5b`で実行した。以下のコマンド欄はrunnerのmetadataに保存されたargvとcwdを展開したものであり、各行のraw欄は必ず`metadata.json`、`stdout.bin`、`stderr.bin`の3ファイルを含む。全gateの`head_before`/`head_after`はこのSHA、dirtyは前後とも空である。

| record | exact command | 結果 | raw |
|---|---|---|---|
| `P0-A-fix-gate-pytest-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-fix-gate-pytest-01` | 15 passed / exit 0 | `.local/coordination/runs/P0-A-fix-gate-pytest-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-fix-gate-format-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .` | 15 files already formatted / exit 0 | `.local/coordination/runs/P0-A-fix-gate-format-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-fix-gate-lint-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .` | All checks passed / exit 0 | `.local/coordination/runs/P0-A-fix-gate-lint-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-fix-gate-mypy-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests` | Success, no issues, 8 source files / exit 0 | `.local/coordination/runs/P0-A-fix-gate-mypy-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-fix-gate-scanner-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` | `PASS`, 17 filesystem files, 17 Git-index files, 0 issues / exit 0 | `.local/coordination/runs/P0-A-fix-gate-scanner-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-fix-gate-build-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build` | sdist and wheel built / exit 0 | `.local/coordination/runs/P0-A-fix-gate-build-01/{metadata.json,stdout.bin,stderr.bin}` |

scannerのraw JSONでは`git_tracking.available=true`、`tracked_files=17`、`index_content_checked=17`、`excluded_tracked_files=0`である。除外領域は`.git`、`.local`、cache、`__pycache__`、`dist`、`febio_cae.egg-info`として報告された。証拠文書の変更はこの後の別commitであり、上記gateは文書変更前のproduction candidateにSHA固定されている。

証拠文書をstageした後のpostcheckも別recordに保存した。`P0-A-fix-report-diff-check-03` は `git diff --check HEAD`、exit 0、raw `.local/coordination/runs/P0-A-fix-report-diff-check-03/{metadata.json,stdout.bin,stderr.bin}` である。`P0-A-fix-report-scanner-03` は下記と同じscanner commandをstage済み文書へ実行し、`PASS`、17 filesystem files、17 Git-index files、0 issues、exit 0を確認した。rawは `.local/coordination/runs/P0-A-fix-report-scanner-03/{metadata.json,stdout.bin,stderr.bin}`。この2つのmetadataは文書stage中の`dirty_before`/`dirty_after`（`M  docs/reviews/2026-09-07-p0-a-bootstrap.md`）と、文書stage前のproduct candidate SHAを明記する。

## wheel と installed smoke

buildログから一意に選択したwheel:

```text
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 11497bbd324a1c32ff179da0266a24bdc4a161bbf4c1b7ce602b243b033682b7
```

次の新規`.local/verification/P0-A-fix-installed-01/installed-env`へPython 3.12のclean venvを作り、wheelをnon-editableで`--no-index --no-deps`インストールした。全ての実行rootは `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness` であり、各metadataのHEADは前後ともfinal product candidate、dirtyは前後とも空である。

```text
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-fix-installed-01\installed-env
```

実行結果とraw証跡:

| record | exact command | 結果 | raw |
|---|---|---|---|
| `P0-A-fix-installed-venv-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m venv C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-fix-installed-01\installed-env` | exit 0 | `.local/coordination/runs/P0-A-fix-installed-venv-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-fix-installed-wheel-hash-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -c "import hashlib, pathlib; p=pathlib.Path(r'C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl'); print(p.name); print(hashlib.sha256(p.read_bytes()).hexdigest())"` | wheel name and SHA above / exit 0 | `.local/coordination/runs/P0-A-fix-installed-wheel-hash-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-fix-installed-pip-01` | `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-fix-installed-01\installed-env\Scripts\python.exe -m pip install --no-index --no-deps --force-reinstall C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl` | `Successfully installed febio-cae-0.1.0` / exit 0 | `.local/coordination/runs/P0-A-fix-installed-pip-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-fix-installed-cli-01` | `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-fix-installed-01\installed-env\Scripts\febio-cae.exe --version` | `febio-cae 0.1.0` / exit 0 | `.local/coordination/runs/P0-A-fix-installed-cli-01/{metadata.json,stdout.bin,stderr.bin}` |

installed importの最終確認は、`PYTHONPATH=P0_A_IMPORT_SENTINEL`、`PYTHONHOME=P0_A_IMPORT_HOME_SENTINEL`をchild processへ明示的に与え、venv Pythonの`-I`で実行した。`-I`はisolated mode（`sys.flags.isolated=1`）を要求し、コマンド内assertはPython 3.12、venvの`site-packages`内のimport元、`__version__ == importlib.metadata.version("febio-cae")`を同時に検証する。

```text
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-fix-installed-01\installed-env\Scripts\python.exe -I -c "import importlib.metadata as metadata, os, pathlib, sys, febio_cae; package_file = pathlib.Path(febio_cae.__file__).resolve(); site_packages = pathlib.Path(sys.prefix).resolve() / 'Lib' / 'site-packages'; print('python=' + sys.executable); print('version=' + sys.version); print('isolated=' + str(sys.flags.isolated)); print('PYTHONPATH=' + repr(os.environ.get('PYTHONPATH'))); print('PYTHONHOME=' + repr(os.environ.get('PYTHONHOME'))); print('package_file=' + str(package_file)); print('metadata_version=' + metadata.version('febio-cae')); print('package_version=' + febio_cae.__version__); print('sys_path=' + repr(sys.path)); assert sys.version_info[:2] == (3, 12); assert sys.flags.isolated == 1; assert package_file.is_relative_to(site_packages); assert febio_cae.__version__ == metadata.version('febio-cae')"
```

`P0-A-fix-installed-import-02` は `exit 0`。stdoutは `isolated=1`、Python `3.12.10`、package file `...installed-env\Lib\site-packages\febio_cae\__init__.py`、`metadata_version=0.1.0`、`package_version=0.1.0`を記録している。metadataには上記2つのsentinel環境値、argv、cwd、start/end、HEAD、dirty、exitを保存した。rawは `.local/coordination/runs/P0-A-fix-installed-import-02/{metadata.json,stdout.bin,stderr.bin}` である。

`P0-A-fix-installed-import-01`もexit 0だったが、最終索引では、より強いinvalid sentinelの`P0-A-fix-installed-import-02`を採用する。過去の`P0-A-installed-01/02`や旧wheel SHAは今回のcandidateの証拠として再利用しない。

## 未検証事項と次タスク

- FEBio、FEBio Studio、Gmshの実体・版・ハッシュ・CLI引数・入力形式・XPLT/FBS互換性は未検証。doctorの不足能力は環境診断であり、P0-Bのnative compatibility evidenceではない。
- 公式FBS、XPLT reader、Studio読込確認、実solver、実LLM、native/E2E-01、許可済み実モデルE2E-02、最終BottomFrame E2E-03は未実施。
- scannerのbounded content detectorは明確なFEBio XML/private-key PEMと境界・readabilityを検査する合成/local gateであり、全形式・全秘密情報・全CAE意味の完全な安全性を保証するものではない。
- wheel smokeは配布されたCLIのversion/importだけを確認し、解析経路や実モデル成功を意味しない。

次タスクは独立Astra Mediumレビューの結果をPMが確認し、受理されたclean commit列だけをV2へ統合すること。P0-Bでは別の許可された範囲でFEBio、Studio、Gmshの実機probeと対応表を作成する。本書更新前の過去候補・過去wheel・過去REDは、今回のfresh candidate証拠として再利用しない。
