# P0-A bootstrap verification

日付: 2026-09-07
対象: FEBio CAE Harness V2 の P0-A（Python 3.12 headless CLI bootstrap）
判定範囲: version、doctor、CAEデータ境界スキャナー、wheel配布、installed smoke

この記録はP0-Aの合成・ローカル証拠であり、実FEBio、公式FBS、FEBio Studio、Gmsh、実モデルの成功証拠ではない。製品仕様の決定元は設計仕様書と実装・検証計画であり、本書は証拠と未検証事項だけを記録する。

## 固定したGit境界

| 項目 | 値 |
|---|---|
| 作業ブランチ | `codex/p0-a-bootstrap` |
| R3 remediation base | `b4d138cd267c2cbdb7e00d12ea0060f64234c75d` |
| R3 test-only SHA | `e7483f93538a4bd6eae1fd586ba31ed12b251c38` |
| R3 production fix SHA | `2622e722c5f0cc2334e90f5965f6dd0950d1a17d` |
| prior R2 product candidate SHA | `30023b86974b54866c40686f9471c06d0f06ef5b` |
| R3 final product candidate SHA | `2622e722c5f0cc2334e90f5965f6dd0950d1a17d` |
| remote | `https://github.com/A6721jpn/cae-harness.git` |
| remote状態 | `REMOTE_CONFIGURED` |
| remote `V2` SHA | `0cc803a6c5a568172662592e5ec932b9c7d09112`（R3開始前の観測値。R3では再取得せず、pushもしていない） |
| fresh gate実行時のGit status | 各metadataの`dirty_before`/`dirty_after`は空。ignoredの`.local/`, `build/`, `dist/`, `*.egg-info/`を除く |

R3のremediationコミット列は次のとおりである。R2までの実装・テスト補正は
prior R2 product candidateに固定されており、R3ではそのcandidateを再利用して
encoded CAE signatureとmetadata failureの境界だけを追加検証した。

1. `e7483f93538a4bd6eae1fd586ba31ed12b251c38` — encoded CAE signature、unsupported encoding、metadata failureの回帰テストのみ。
2. `2622e722c5f0cc2334e90f5965f6dd0950d1a17d` — scannerのXML comment/PI、UTF-16、STEP、XPLT、encrypted RSA PEM、filesystem metadata errorのproduction fix。

R3で追加されたproduct変更は`src/febio_cae/cli/scan_cae_data.py`だけであり、回帰テストは
`tests/unit/test_scan_cae_data.py`だけである。prior R2までの変更を含むfinal product
candidateの許可範囲は`pyproject.toml`、`src/febio_cae/cli/doctor.py`、
`src/febio_cae/cli/scan_cae_data.py`、`tests/unit/test_bootstrap.py`、
`tests/unit/test_scan_cae_data.py`である。`.gitignore`、設計仕様書、実データ領域、
V2へのpushは変更していない。今回の証拠文書更新はこの範囲外の許可された
`docs/reviews/2026-09-07-p0-a-bootstrap.md`だけに限定する。

## RED / GREEN

実行インタープリターは `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe`、Python `3.12.10` である。今回の実行はすべて、同じ作業root `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness` で、runner metadataに展開済みargv、cwd、HEAD、dirty state、UTC start/end、exit code、stdout/stderrの絶対パスを保存した。

### 過去証跡の限定

旧版候補 `3782d1b7aa37d2774f6e9e1971977f35477fd02e` の旧RED（5 collected / 5 failed / exit 1）は、入口未実装・import/entrypoint不在を含む初期骨格の証拠であり、今回のH1/H2/M1/M2/M3に対する厳格な動作REDではない。R2の動作REDも、6件のbehavior assertion failureに加えて2件の`KeyError`と1件のmissing-helper `AttributeError`を含み、R3のstrict behavior REDとして再利用しない。R2のinstalled CLI確認はrepo cwdで実行され、isolated cwdの証明ではないため、R3のinstalled smokeにも再利用しない。旧runtime CommandExecution出力に実在したexit codeの補正と、実行時SHAの事前記録が欠けていることは別問題であり、旧REDを今回のfresh証跡へ置き換えたり、元SHAを後付けしたりしない。旧`P0-A-red-01`のWindows setup errorも合格証拠には数えない。

### 今回の動作RED

test-only SHA `e7483f93538a4bd6eae1fd586ba31ed12b251c38` でR3回帰テストを追加した後、
basetempが事前に存在しないことを`P0-A-r3-red-basetemp-preflight-01`（exit 0）で確認し、
次を実行した。

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/test_scan_cae_data.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-r3-red-01
```

結果は `20 collected / 10 failed / 10 passed / exit 1`。10件の失敗はすべて`AssertionError`であり、collection error、`KeyError`、`AttributeError`、setup errorではない。失敗対象はXML comment/processing instruction、UTF-16 LE/BE、STEP、XPLT、encrypted RSA PEM、unsupported XML encoding、worktree XML、filesystem metadata failureである。全ログは `.local/coordination/runs/P0-A-r3-red-01/metadata.json`、`stdout.bin`、`stderr.bin`。metadataの`head_before`/`head_after`は同じ `e7483f93538a4bd6eae1fd586ba31ed12b251c38`、dirtyは前後とも空である。

### 今回のGREEN

final product candidate `2622e722c5f0cc2334e90f5965f6dd0950d1a17d` で、basetempが事前に存在しないことを
`P0-A-r3-green-basetemp-preflight-01`（exit 0）で確認した後、次を実行した。

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/test_scan_cae_data.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-r3-green-01
```

結果は `20 passed / exit 0`。freshログは `.local/coordination/runs/P0-A-r3-green-01/metadata.json`、`stdout.bin`、`stderr.bin`。metadataのHEADは前後とも `2622e722c5f0cc2334e90f5965f6dd0950d1a17d`、dirtyは前後とも空である。

標準testpaths（`tests/unit`, `tests/component`）全体のfresh実行も同じcandidateで行い、`25 passed / exit 0`だった。正確なargvとraw outputは、下記gate表の `P0-A-r3-gate-pytest-01` に固定した。

## 実装した契約

- `python -m febio_cae --version` とインストール後の`febio-cae --version`は`febio-cae 0.1.0`を返す。
- `doctor --json`は`schema_version`、`case_id`、`revision_id`、`run_id`、能力状態、診断、次のアクションをJSONで返す。FEBio、FEBio Studio、Gmshのいずれかが不足すると`status=UNAVAILABLE`、exit code 4となる。存在するだけのinert fileは`FOUND_UNVERIFIED`、全能力が未検証なら`status=UNVERIFIED`、検証用next action付き、exit code 4となる。`doctor`のデフォルト出力はhuman-readableで、`--json`だけがmachine-readable出力を選ぶ。
- `scripts/scan_cae_data.py --root <root>`はtracked path、Git index blob、current filesystemを検査し、`02_CAE`、CAE結果・モデル拡張子、credential/token/desktop状態、trackedされた除外領域を拒否する。Git root/indexの取得失敗、blobのread/size超過、未知Git mode、filesystem traversal/read error、symlink/Windows reparse pointは`INCOMPLETE`かつexit code 4で、成功扱いにしない。bounded detectorは明確なFEBio XML root/close（comment/processing instruction付きとUTF-16 LE/BEを含む）、STEP、XPLTの`BEF` signature、十分なencrypted RSA private-key PEM bodyを検出し、単なる文書上の言及はfalse positive controlとして許容する。unsupported XML encodingは`INCOMPLETE`、filesystem `lstat`失敗は`FILESYSTEM_METADATA_ERROR`診断を伴う`INCOMPLETE`として扱う。`.git`、`.local`、build/dist、cache、venv等の除外を結果へ明示する。
- `pyproject.toml`は`febio`、`llm`、`native`、`e2e` markerを登録し、native/e2eを標準testpathsへ追加しない。

## 必須ローカルゲート

全gateはfinal product candidate `2622e722c5f0cc2334e90f5965f6dd0950d1a17d`で実行した。pytestのbasetempが事前に存在しないことは`P0-A-r3-gate-pytest-basetemp-preflight-01`（exit 0）で確認した。以下のコマンド欄はrunnerのmetadataに保存されたargvとcwdを展開したものであり、各行のraw欄は必ず`metadata.json`、`stdout.bin`、`stderr.bin`の3ファイルを含む。全gateの`head_before`/`head_after`はこのSHA、dirtyは前後とも空である。各metadataには、ここに展開したargv/cwdに加え、UTC `started_at_utc`、`finished_at_utc`、`exit_code`、HEAD、dirty state、stdout/stderrの絶対パスを保存している。

| record | exact command | 結果 | raw |
|---|---|---|---|
| `P0-A-r3-gate-pytest-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-r3-gate-pytest-01` | 25 passed / exit 0 | `.local/coordination/runs/P0-A-r3-gate-pytest-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r3-gate-format-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .` | 15 files already formatted / exit 0 | `.local/coordination/runs/P0-A-r3-gate-format-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r3-gate-lint-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .` | All checks passed / exit 0 | `.local/coordination/runs/P0-A-r3-gate-lint-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r3-gate-mypy-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests` | Success, no issues, 8 source files / exit 0 | `.local/coordination/runs/P0-A-r3-gate-mypy-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r3-gate-scanner-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` | `PASS`, 17 filesystem files, 17 Git-index files, 0 issues / exit 0 | `.local/coordination/runs/P0-A-r3-gate-scanner-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r3-gate-build-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build` | sdist and wheel built / exit 0 | `.local/coordination/runs/P0-A-r3-gate-build-01/{metadata.json,stdout.bin,stderr.bin}` |

scannerのraw JSONでは`git_tracking.available=true`、`tracked_files=17`、`index_content_checked=17`、`excluded_tracked_files=0`である。除外領域は`.git`、`.local`、cache、`__pycache__`、`dist`、`febio_cae.egg-info`として報告された。証拠文書の変更はこの後の別commitであり、上記gateは文書変更前のproduction candidateにSHA固定されている。

証拠文書をstageした後の最終postcheckは`P0-A-r3-report-diff-check-02`で
`git diff --cached --check`を実行し、exit 0を確認した。続く
`P0-A-r3-report-scanner-02`は`C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .`
を同じstage済み文書へ実行し、`PASS`、17 filesystem files、17 Git-index files、0 issues、
exit 0を確認した。両recordのmetadataは文書stage中の
`dirty_before`/`dirty_after`（`M  docs/reviews/2026-09-07-p0-a-bootstrap.md`）と、
文書stage前のproduct candidate SHA、exact argv/cwd、UTC start/end、exit code、
stdout/stderr絶対パスを明記する。rawは各々の
`.local/coordination/runs/<record>/{metadata.json,stdout.bin,stderr.bin}`に保存する。

## wheel と installed smoke

buildログから一意に選択したwheel:

```text
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl
SHA-256: 9289028973a82dff1a9f600a1ed4685b0e4a503f7880460466037fe8d4c9af0b
```

次の新規`.local/verification/P0-A-r3-installed-01/installed-env`へPython 3.12のclean venvを作り、wheelをnon-editableで`--no-index --no-deps`インストールした。pipとCLIはrepo root直下の作業cwdではなく、repo内ignored verification subdirにある新規`installed-cwd`から実行した。全ての実行rootは `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness` であり、各metadataのHEADは前後ともfinal product candidate、dirtyは前後とも空である。新規env/cwdが事前に存在しないことは`P0-A-r3-installed-preflight-01`（exit 0）、cwd作成は`P0-A-r3-installed-cwd-create-01`（exit 0）で記録した。

```text
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-r3-installed-01\installed-env
```

実行結果とraw証跡:

| record | exact command | 結果 | raw |
|---|---|---|---|
| `P0-A-r3-installed-venv-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m venv C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-r3-installed-01\installed-env` | exit 0 | `.local/coordination/runs/P0-A-r3-installed-venv-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r3-installed-wheel-hash-01` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -c "import hashlib, pathlib; p=pathlib.Path(r'C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl'); print(p.name); print(hashlib.sha256(p.read_bytes()).hexdigest())"` | wheel name and SHA above / exit 0 | `.local/coordination/runs/P0-A-r3-installed-wheel-hash-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r3-installed-pip-01` | `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-r3-installed-01\installed-env\Scripts\python.exe -m pip install --no-index --no-deps --force-reinstall C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl` | `Successfully installed febio-cae-0.1.0` / exit 0 | `.local/coordination/runs/P0-A-r3-installed-pip-01/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r3-installed-cli-01` | `C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-r3-installed-01\installed-env\Scripts\febio-cae.exe --version` | `febio-cae 0.1.0` / exit 0 | `.local/coordination/runs/P0-A-r3-installed-cli-01/{metadata.json,stdout.bin,stderr.bin}` |

installed importの最終確認は、repo内ignored verification subdirの`installed-cwd`からvenv Pythonの`-I`で実行した。`-I`はisolated mode（`sys.flags.isolated=1`）を要求し、コマンド内assertはPython 3.12、venvの`site-packages`内のimport元、`__version__ == importlib.metadata.version("febio-cae")`を同時に検証する。stdoutには`PYTHONPATH=None`、`PYTHONHOME=None`も記録された。

```text
C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-r3-installed-01\installed-env\Scripts\python.exe -I -c "import importlib.metadata as metadata, os, pathlib, sys, febio_cae; package_file = pathlib.Path(febio_cae.__file__).resolve(); site_packages = pathlib.Path(sys.prefix).resolve() / 'Lib' / 'site-packages'; print('python=' + sys.executable); print('version=' + sys.version); print('isolated=' + str(sys.flags.isolated)); print('PYTHONPATH=' + repr(os.environ.get('PYTHONPATH'))); print('PYTHONHOME=' + repr(os.environ.get('PYTHONHOME'))); print('package_file=' + str(package_file)); print('metadata_version=' + metadata.version('febio-cae')); print('package_version=' + febio_cae.__version__); print('sys_path=' + repr(sys.path)); assert sys.version_info[:2] == (3, 12); assert sys.flags.isolated == 1; assert package_file.is_relative_to(site_packages); assert febio_cae.__version__ == metadata.version('febio-cae')"
```

`P0-A-r3-installed-import-01` は `exit 0`。stdoutは `isolated=1`、Python `3.12.10`、`PYTHONPATH=None`、`PYTHONHOME=None`、package file `...installed-env\Lib\site-packages\febio_cae\__init__.py`、`metadata_version=0.1.0`、`package_version=0.1.0`を記録している。metadataにはargv、cwd、start/end、HEAD、dirty、exitを保存した。rawは `.local/coordination/runs/P0-A-r3-installed-import-01/{metadata.json,stdout.bin,stderr.bin}` である。

過去の`P0-A-fix-installed-*`、`P0-A-installed-01/02`や旧wheel SHAは今回のcandidateの証拠として再利用しない。

## 未検証事項と次タスク

- FEBio、FEBio Studio、Gmshの実体・版・ハッシュ・CLI引数・入力形式・XPLT/FBS互換性は未検証。doctorの不足能力は環境診断であり、P0-Bのnative compatibility evidenceではない。
- 公式FBS、XPLT reader、Studio読込確認、実solver、実LLM、native/E2E-01、許可済み実モデルE2E-02、最終BottomFrame E2E-03は未実施。
- scannerのbounded content detectorは明確なFEBio XML/private-key PEMと境界・readabilityを検査する合成/local gateであり、全形式・全秘密情報・全CAE意味の完全な安全性を保証するものではない。
- wheel smokeは配布されたCLIのversion/importだけを確認し、解析経路や実モデル成功を意味しない。

次タスクは独立Astra Mediumレビューの結果をPMが確認し、受理されたclean commit列だけをV2へ統合すること。P0-Bでは別の許可された範囲でFEBio、Studio、Gmshの実機probeと対応表を作成する。本書更新前の過去候補・過去wheel・過去REDは、今回のfresh candidate証拠として再利用しない。

## R4 High findings remediation appendix

R3 independent reviewのHigh 2（XML candidate preclassification gap、誤った7-byte XPLT fixture/signature）に対するR4の差分だけを追記する。R3の履歴と証拠は上記のまま保持し、R3の誤った11-byte XPLT fixtureを実XPLT signatureの証拠として再利用しない。

### R4 commit boundary

| 項目 | 値 |
|---|---|
| R4 base | `b7a45b69949d55cc19fc8884b704e4ee42483b1c` |
| R4 test-only | `e7137bc356060a4b56b2e0785d2720a7ca02593c`（parent: R4 base） |
| R4 production | `6044b97f33554c3d7545c0e1082d1117ba08dca4`（parent: test-only） |
| test format correction | `c672bc34af121de280959ebcdc39b263ccb2b163`、`afcd6e2d98e86d0762ef1ae23576052990d0a9e4` |
| final code candidate | `afcd6e2d98e86d0762ef1ae23576052990d0a9e4` |
| R4 tracked code/test files | `src/febio_cae/cli/scan_cae_data.py`、`tests/unit/test_scan_cae_data.py` |

Productionの変更は、既存の1 MiB bounded inspection内で先頭ASCII whitespaceを全体から除去してcandidate判定すること、DOCTYPE/CDATA先頭を安全なdecode後の`UNINSPECTABLE_CONTENT`経路へ渡すこと、XPLT magicを`BEF 00 00 00 00 01`の8-byte prefixへ修正することだけである。test-only fixtureはproduction constantを複製せず、`struct.pack("<III", 0x00464542, 0x01000000, 0)`から独立に12 bytesを生成し、length `12`とprefix hex `4245460000000001`をassertして、Git index/worktreeの両経路を検証する。

### R4 TDD evidence

test-only SHA `e7137bc356060a4b56b2e0785d2720a7ca02593c`で、basetemp不在を`P0-A-r4-red-basetemp-preflight-01`（exit 0）で確認後、次を実行した。

```text
C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest tests/unit/test_scan_cae_data.py --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-r4-red-01
```

結果は`29 collected / 8 failed / 21 passed / exit 1`。8件はすべてAssertionErrorで、collection/environment/setup errorはない。失敗は512/600 leading-space XMLのindex/worktree各2件、DOCTYPE-first XMLのindex/worktree各1件、独立12-byte XPLT fixtureのindex/worktree各1件であり、500-space controlは通過した。rawは`.local/coordination/runs/P0-A-r4-red-01/{metadata.json,stdout.bin,stderr.bin}`、HEAD/dirtyは前後ともtest-only SHA/cleanである。

production `6044b97f33554c3d7545c0e1082d1117ba08dca4`後、test format-only corrections `c672bc34af121de280959ebcdc39b263ccb2b163`、`afcd6e2d98e86d0762ef1ae23576052990d0a9e4`を経た最終candidateで、basetemp不在を`P0-A-r4-green-basetemp-preflight-03`（exit 0）で確認し、`P0-A-r4-green-03`として同じfocused commandを実行した。結果は`29 passed / exit 0`、HEAD/dirtyは前後とも`afcd6e2d98e86d0762ef1ae23576052990d0a9e4`/cleanである。各recordのmetadataはexact argv/cwd、Python、UTC start/end、exit、前後SHA/dirty、stdout/stderr絶対パスを保存する。

### R4 final candidate gates

最終candidate `afcd6e2d98e86d0762ef1ae23576052990d0a9e4`で、full pytest basetemp不在を`P0-A-r4-gate-pytest-basetemp-preflight-03`（exit 0）で確認した。fresh gateは以下のとおりである。

| record | exact command | 結果 | raw |
|---|---|---|---|
| `P0-A-r4-gate-pytest-03` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m pytest --basetemp C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\.local\verification\P0-A-r4-gate-pytest-03` | 34 passed / exit 0 | `.local/coordination/runs/P0-A-r4-gate-pytest-03/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r4-gate-format-03` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff format --check .` | 15 files already formatted / exit 0 | `.local/coordination/runs/P0-A-r4-gate-format-03/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r4-gate-lint-03` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m ruff check .` | All checks passed / exit 0 | `.local/coordination/runs/P0-A-r4-gate-lint-03/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r4-gate-mypy-03` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m mypy src tests` | Success, no issues, 8 source files / exit 0 | `.local/coordination/runs/P0-A-r4-gate-mypy-03/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r4-gate-scanner-03` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .` | `PASS`, 17 filesystem files, 17 Git-index files, 0 issues / exit 0 | `.local/coordination/runs/P0-A-r4-gate-scanner-03/{metadata.json,stdout.bin,stderr.bin}` |
| `P0-A-r4-gate-build-02` | `C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe -m build` | sdist and wheel built / exit 0 | `.local/coordination/runs/P0-A-r4-gate-build-02/{metadata.json,stdout.bin,stderr.bin}` |

R4 formatの履歴では、`P0-A-r4-gate-format-01`（HEAD `6044b97f33554c3d7545c0e1082d1117ba08dca4`）と`P0-A-r4-gate-format-02`（HEAD `c672bc34af121de280959ebcdc39b263ccb2b163`）がいずれもexit 1であり、各rawは`.local/coordination/runs/<record>/{metadata.json,stdout.bin,stderr.bin}`にある。srcの機能は変えずにtest-format-only correction `afcd6e2d98e86d0762ef1ae23576052990d0a9e4`を適用した後、`P0-A-r4-gate-format-03`は15 files already formatted / exit 0となった。

### R4 installed smoke

wheelは`C:\Users\backo\.codex\worktrees\8dd5\CAE-harness\dist\febio_cae-0.1.0-py3-none-any.whl`、SHA-256は`03077f7e3c25bf42dc2b2ac8c65ab8a0fa7422cee4d1ac73d481231a0612fb45`である。新規`.local/verification/P0-A-r4-installed-02/installed-env`へnon-editable installし、pip/CLI/importはrepo内ignored verification subdirの新規`installed-cwd`から実行した。このcwdはrepo root直下ではなく、installed source packageを探索しないisolated実行境界である。

`P0-A-r4-installed-preflight-02`、`P0-A-r4-installed-cwd-create-02`、`P0-A-r4-installed-venv-02`、`P0-A-r4-installed-wheel-hash-02`、`P0-A-r4-installed-pip-02`、`P0-A-r4-installed-cli-02`、`P0-A-r4-installed-import-02`はすべてexit 0である。CLI stdoutは`febio-cae 0.1.0`。importは`-I`、Python `3.12.10`、`isolated=1`、`PYTHONPATH=None`、`PYTHONHOME=None`、package fileがinstalled-envの`site-packages`内、metadata/package versionがともに`0.1.0`を記録し、assertもexit 0である。各rawは`.local/coordination/runs/<record>/{metadata.json,stdout.bin,stderr.bin}`にあり、metadataはexact argv/cwd/Python/UTC start/end/exit/前後SHA/dirty/絶対stdout/stderrを保存する。

証拠文書をstageした後、`P0-A-r4-report-diff-check-02`で`git diff --cached --check`を実行しexit 0、`P0-A-r4-report-scanner-02`で`C:\Users\backo\AppData\Local\Programs\Python\Python312\python.exe scripts/scan_cae_data.py --root .`を実行しexit 0を確認した。scannerは17 filesystem files/17 Git-index files/0 issuesであり、rawは各recordの`.local/coordination/runs/<record>/{metadata.json,stdout.bin,stderr.bin}`に保存した。両metadataはstage中の`dirty_before`/`dirty_after`、final code candidate SHA、exact argv/cwd/Python、UTC start/end、exit、stdout/stderr絶対パスを保持する。

R4でも実FEBio/FBS/Studio/Gmsh/LLM、native/E2E、real `02_CAE`、BottomFrame、完全XPLT reader、実モデル成功は未検証であり、synthetic/local boundary evidenceを実CAE成功として扱わない。
